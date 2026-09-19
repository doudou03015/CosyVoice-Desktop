"""Real loopback transfers cover interrupted setup and verified offline reuse."""
import hashlib
import http.server
import io
from pathlib import Path
import socket
import threading
from urllib.parse import urlparse
import zipfile

import pytest

from desktop_app import downloads


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def environment(tmp_path, tmp_path_factory, monkeypatch):
    # Keep SHA-named cache files below Windows MAX_PATH even with a long
    # task-specific pytest base directory and descriptive test names.
    session = tmp_path_factory.mktemp('dl') / 'session'
    session.mkdir()
    root = tmp_path / 'application'
    root.mkdir()
    monkeypatch.setattr(downloads, 'session_dir', lambda: session)
    monkeypatch.setattr(downloads, 'app_root', lambda: root)
    monkeypatch.setattr(downloads, 'RETRY_DELAYS', (0, 0))
    monkeypatch.setenv('COSYVOICE_TEST_HTTP', '1')
    return root, session


@pytest.fixture
def serve(environment):
    servers = []
    def start(payload, *, mode='normal', status=401, forbidden=()):
        requests = []
        order = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except ConnectionError:
                    pass  # A timed-out/cancelled client intentionally closes its socket.

            def do_GET(self):
                requests.append(self.headers.get('Range'))
                order.append('request')
                path = urlparse(self.path).path
                if mode == 'denied' or path in forbidden or (mode == 'status_once' and len(requests) == 1):
                    self.send_response(status)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if mode == 'timeout_once' and len(requests) == 1:
                    threading.Event().wait(.12)
                if mode == 'disconnect_once' and len(requests) == 1:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if mode == 'full_body_disconnect':
                    if len(requests) == 1:
                        self.send_response(200)
                        self.send_header('Transfer-Encoding', 'chunked')
                        self.end_headers()
                        # All bytes arrive, but the required terminating chunk does not.
                        self.wfile.write(f'{len(payload):x}\r\n'.encode() + payload + b'\r\n')
                        self.wfile.flush()
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                    else:
                        self.send_response(416)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    return
                offset = int(self.headers.get('Range', 'bytes=0-')[6:].split('-')[0])
                if mode == 'ignore_range':
                    offset = 0
                self.send_response(206 if offset else 200)
                if offset:
                    returned_offset = offset + 1 if mode == 'wrong_range' else offset
                    self.send_header('Content-Range', f'bytes {returned_offset}-{len(payload) - 1}/{len(payload)}')
                self.send_header('Content-Length', str(len(payload) - offset))
                self.end_headers()
                remaining = payload[offset:]
                if mode == 'incomplete' or (mode == 'interrupt_once' and len(requests) == 1):
                    remaining = remaining[:4096]
                try:
                    self.wfile.write(remaining)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                if mode in ('incomplete', 'interrupt_once'):
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass  # A client may already have closed after consuming/cancelling.
                    self.connection.close()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f'http://127.0.0.1:{server.server_port}/weights.bin?signature=PRIVATE_QUERY', requests, order
    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def spec_for(url, payload, **entry):
    return {'id': 'model-cosyvoice3', 'version': 'fixed-revision', 'files': [
        dict(path='nested/weights.bin', url=url, sha256=digest(payload), size=len(payload), **entry)]}


def cache_path(session, spec, payload):
    return session.parent / 'CosyVoice-Desktop-downloads' / (spec['id'] + '-' + spec['version']) / digest(payload)


@pytest.mark.parametrize('partial', [False, True])
def test_complete_cache_or_completed_partial_performs_zero_requests(environment, serve, tmp_path, partial):
    payload = b'verified payload' * 100
    url, requests, order = serve(payload, mode='denied')
    target = tmp_path / 'download'
    saved = target.with_name(target.name + '.part') if partial else target
    saved.write_bytes(payload)
    events = []
    assert downloads.download_file(url, target, digest(payload), len(payload), events.append) == target
    assert target.read_bytes() == payload
    assert requests == []
    assert any('直接复用' in event['message'] for event in events)
    assert not target.with_name(target.name + '.part').exists()


@pytest.mark.parametrize('mode', ['normal', 'ignore_range'])
def test_resume_and_server_ignoring_range_preserve_exact_contents(environment, serve, tmp_path, mode):
    payload = b'0123456789' * 10000
    url, requests, order = serve(payload, mode=mode)
    target = tmp_path / 'download'
    target.with_name('download.part').write_bytes(payload[:1234])
    events = []
    def progress(event):
        events.append(event)
        order.append(event['stage'])
    downloads.download_file(url, target, digest(payload), len(payload), progress)
    assert requests == ['bytes=1234-']
    assert order.index('connect') < order.index('request')
    assert target.read_bytes() == payload


def test_mid_stream_disconnect_retries_with_range_from_saved_bytes(environment, serve, tmp_path):
    payload = b'streaming model bytes' * 10000
    url, requests, order = serve(payload, mode='interrupt_once')
    target = tmp_path / 'download'
    events = []
    downloads.download_file(url, target, digest(payload), len(payload), events.append)
    assert requests == [None, 'bytes=4096-']
    assert target.read_bytes() == payload
    assert any(event['stage'] == 'retry' for event in events)


def test_disconnection_after_complete_chunked_body_verifies_partial_before_retry(environment, serve, tmp_path):
    payload = b'complete file with missing transport terminator' * 100
    url, requests, order = serve(payload, mode='full_body_disconnect')
    target = tmp_path / 'download'
    downloads.download_file(url, target, digest(payload), len(payload))
    assert target.read_bytes() == payload
    assert requests == [None]
    assert not target.with_name('download.part').exists()


def test_repeated_short_responses_preserve_partial_and_stop_retrying(environment, serve, tmp_path):
    payload = b'x' * 50000
    url, requests, order = serve(payload, mode='incomplete')
    target = tmp_path / 'download'
    with pytest.raises(downloads.DownloadError, match='响应不完整'):
        downloads.download_file(url, target, digest(payload), len(payload))
    assert len(requests) == downloads.MAX_DOWNLOAD_ATTEMPTS == 3
    assert target.with_name('download.part').read_bytes() == payload[:3 * 4096]
    assert not target.exists()


@pytest.mark.parametrize('status', [408, 429, 503])
def test_transient_http_status_retries_and_succeeds(environment, serve, tmp_path, status):
    payload = b'retry succeeds'
    url, requests, order = serve(payload, mode='status_once', status=status)
    target = tmp_path / 'download'
    downloads.download_file(url, target, digest(payload), len(payload))
    assert len(requests) == 2
    assert target.read_bytes() == payload


@pytest.mark.parametrize('mode', ['timeout_once', 'disconnect_once'])
def test_connection_timeout_or_disconnect_can_retry_without_publishing_incomplete_file(environment, serve, tmp_path, monkeypatch, mode):
    payload = b'complete after network recovery'
    url, requests, order = serve(payload, mode=mode)
    monkeypatch.setattr(downloads, 'NETWORK_TIMEOUT', .03)
    target = tmp_path / 'download'
    events = []
    downloads.download_file(url, target, digest(payload), len(payload), events.append)
    assert target.read_bytes() == payload
    assert len(requests) == 2
    assert any(event['stage'] == 'retry' for event in events)


def test_bad_hash_cannot_publish_new_file_or_damage_previous_install(environment, serve, tmp_path):
    expected = b'valid model weights'
    wrong = b'X' * len(expected)
    url, requests, order = serve(wrong)
    spec = spec_for(url, expected)
    previous = tmp_path / 'components' / 'model-cosyvoice3-fixed-revision'
    previous.mkdir(parents=True)
    (previous / 'unrelated.txt').write_bytes(b'previous installed data')
    with pytest.raises(downloads.DownloadError, match='SHA256'):
        downloads.install_package(spec, previous.parent)
    assert (previous / 'unrelated.txt').read_bytes() == b'previous installed data'
    assert not (previous / '.complete.json').exists()
    assert not cache_path(environment[1], spec, expected).exists()
    assert not cache_path(environment[1], spec, expected).with_name(digest(expected) + '.part').exists()
    assert len(requests) == 1


@pytest.mark.parametrize('status', [401, 403])
def test_permanent_http_error_names_spec_file_host_status_and_keeps_cache(environment, serve, tmp_path, status):
    payload = b'correct content' * 100
    url, requests, order = serve(payload, mode='denied', status=status)
    spec = spec_for(url, payload)
    spec['files'][0]['path'] = 'wetext/zh/tn/tagger.fst'
    cached = cache_path(environment[1], spec, payload)
    cached.parent.mkdir(parents=True)
    partial = cached.with_name(cached.name + '.part')
    partial.write_bytes(payload[:17])
    events = []
    with pytest.raises(downloads.DownloadError) as failure:
        downloads.install_package(spec, tmp_path / 'components', events.append)
    message = str(failure.value)
    assert 'wetext/zh/tn/tagger.fst' in message
    assert '127.0.0.1' in message and f'HTTP {status}' in message and '保留' in message
    assert 'PRIVATE_QUERY' not in message and 'signature=' not in message
    assert partial.read_bytes() == payload[:17]
    assert requests == ['bytes=17-']
    connection = next(event for event in events if event['stage'] == 'connect')
    assert 'wetext/zh/tn/tagger.fst' in connection['message']


def test_wrong_range_never_appends_to_previous_partial(environment, serve, tmp_path):
    payload = b'correct content' * 100
    url, requests, order = serve(payload, mode='wrong_range')
    target = tmp_path / 'download'
    partial = target.with_name('download.part')
    partial.write_bytes(payload[:17])
    with pytest.raises(ValueError, match='续传位置'):
        downloads.download_file(url, target, digest(payload), len(payload))
    assert partial.read_bytes() == payload[:17]
    assert len(requests) == 1


def test_cancelling_after_first_block_keeps_resumable_partial(environment, serve, tmp_path):
    payload = b'data' * 200000
    url, requests, order = serve(payload)
    target = tmp_path / 'download'
    cancelled = threading.Event()
    def progress(event):
        if event['stage'] == 'download':
            cancelled.set()
    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_file(url, target, digest(payload), len(payload), progress, cancelled)
    partial = target.with_name('download.part').read_bytes()
    assert partial and payload.startswith(partial)
    assert not target.exists()
    assert len(requests) == 1


def test_cancellation_stops_retry_backoff_without_second_request(environment, serve, tmp_path, monkeypatch):
    payload = b'retry'
    url, requests, order = serve(payload, mode='denied', status=503)
    cancelled = threading.Event()
    monkeypatch.setattr(downloads, 'RETRY_DELAYS', (30, 30))
    def progress(event):
        if event['stage'] == 'retry':
            cancelled.set()
    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_file(url, tmp_path / 'download', digest(payload), len(payload), progress, cancelled)
    assert len(requests) == 1


def test_reinstall_after_desktop_version_change_reuses_same_component_cache(environment, serve, tmp_path, monkeypatch):
    payload = b'pinned component' * 500
    url, requests, order = serve(payload)
    spec = spec_for(url, payload)
    first = downloads.install_package(spec, tmp_path / 'first-components')
    assert len(requests) == 1
    monkeypatch.setattr(downloads, '__version__', '0.1.1')
    events = []
    second = downloads.install_package(spec, tmp_path / 'second-components', events.append)
    assert first.name == second.name == 'model-cosyvoice3-fixed-revision'
    assert cache_path(environment[1], spec, payload).read_bytes() == payload
    assert (second / 'nested/weights.bin').read_bytes() == payload
    assert len(requests) == 1
    assert any(event['stage'] == 'cache' and event['file'] == 'nested/weights.bin' for event in events)
    downloads.install_package(spec, tmp_path / 'second-components')
    assert len(requests) == 1


def test_split_archive_reuses_verified_parts_and_resumes_last_part(environment, serve, tmp_path):
    payload = b'portable runtime payload' * 100
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('runtime/python.exe', payload)
    archive_bytes = archive.getvalue()
    first_part = archive_bytes[:len(archive_bytes) // 2]
    second_part = archive_bytes[len(first_part):]
    first_url, first_requests, _ = serve(first_part, mode='denied')
    second_url, second_requests, _ = serve(second_part)
    spec = dict(id='runtime-cu121', version='stable-runtime', strip_prefix='runtime',
                assets=[dict(url=first_url, size=len(first_part), sha256=digest(first_part)),
                        dict(url=second_url, size=len(second_part), sha256=digest(second_part))],
                files=[dict(path='python.exe', size=len(payload), sha256=digest(payload))])
    cache = environment[1].parent / 'CosyVoice-Desktop-downloads' / 'runtime-cu121-stable-runtime'
    cache.mkdir(parents=True)
    (cache / 'part-000').write_bytes(first_part)
    (cache / 'part-001.part').write_bytes(second_part[:31])
    installed = downloads.install_package(spec, tmp_path / 'first-components')
    assert (installed / 'python.exe').read_bytes() == payload
    assert first_requests == []
    assert second_requests == ['bytes=31-']
    assert (cache / 'part-001').read_bytes() == second_part
    reinstalled = downloads.install_package(spec, tmp_path / 'other-components')
    assert (reinstalled / 'python.exe').read_bytes() == payload
    assert first_requests == [] and second_requests == ['bytes=31-']


def test_401_on_later_file_keeps_earlier_download_and_previous_install(environment, serve, tmp_path):
    payload = b'first complete file' * 300
    url, requests, order = serve(payload, forbidden=('/blocked',))
    spec = spec_for(url, payload)
    spec['files'].append(dict(path='later.bin', url=url.replace('/weights.bin', '/blocked'), size=5, sha256=digest(b'later')))
    previous = tmp_path / 'components' / 'model-cosyvoice3-fixed-revision'
    previous.mkdir(parents=True)
    original = previous / 'keep.txt'
    original.write_bytes(b'original installation')
    with pytest.raises(downloads.DownloadError, match='later.bin'):
        downloads.install_package(spec, previous.parent)
    assert cache_path(environment[1], spec, payload).read_bytes() == payload
    assert original.read_bytes() == b'original installation'
    assert len(requests) == 2


def test_verified_bundled_resource_installs_without_contacting_unauthorized_url(environment, serve, tmp_path):
    payload = b'offline text normalization data'
    url, requests, order = serve(payload, mode='denied')
    resource = environment[0] / 'resources' / 'tagger.fst'
    resource.parent.mkdir()
    resource.write_bytes(payload)
    spec = spec_for(url, payload, bundled_path='resources/tagger.fst')
    result = downloads.install_package(spec, tmp_path / 'components')
    assert (result / 'nested/weights.bin').read_bytes() == payload
    assert requests == []


@pytest.mark.parametrize('failure', ['missing', 'wrong_hash', 'wrong_size', 'directory', 'escape', 'absolute'])
def test_invalid_bundled_resource_fails_without_network_fallback(environment, serve, tmp_path, failure):
    payload = b'offline resource'
    url, requests, order = serve(payload, mode='denied')
    resource = environment[0] / 'tagger.fst'
    bundled_path = 'tagger.fst'
    if failure == 'wrong_hash':
        resource.write_bytes(b'X' * len(payload))
    elif failure == 'wrong_size':
        resource.write_bytes(b'short')
    elif failure == 'directory':
        resource.mkdir()
    elif failure in ('escape', 'absolute'):
        outside = tmp_path / 'outside.fst'
        outside.write_bytes(payload)
        bundled_path = '../outside.fst' if failure == 'escape' else str(outside)
    spec = spec_for(url, payload, bundled_path=bundled_path)
    with pytest.raises(ValueError, match='nested/weights.bin'):
        downloads.install_package(spec, tmp_path / 'components')
    assert requests == []
    assert not (tmp_path / 'components' / 'model-cosyvoice3-fixed-revision').exists()


def test_symlinked_bundled_resource_is_rejected_even_inside_application(environment, serve, tmp_path):
    payload = b'offline resource'
    url, requests, order = serve(payload, mode='denied')
    resource = environment[0] / 'original.fst'
    resource.write_bytes(payload)
    link = environment[0] / 'linked.fst'
    try:
        link.symlink_to(resource)
    except OSError:
        pytest.skip('symlink unavailable')
    spec = spec_for(url, payload, bundled_path='linked.fst')
    with pytest.raises(ValueError, match='链接'):
        downloads.install_package(spec, tmp_path / 'components')
    assert requests == []


@pytest.mark.parametrize('partial', [False, True])
def test_valid_cache_can_replace_missing_bundled_resource(environment, serve, tmp_path, partial):
    payload = b'verified previous download'
    url, requests, order = serve(payload, mode='denied')
    spec = spec_for(url, payload, bundled_path='missing.fst')
    cached = cache_path(environment[1], spec, payload)
    cached.parent.mkdir(parents=True)
    saved = cached.with_name(cached.name + '.part') if partial else cached
    saved.write_bytes(payload)
    result = downloads.install_package(spec, tmp_path / 'components')
    assert (result / 'nested/weights.bin').read_bytes() == payload
    assert cached.read_bytes() == payload
    assert requests == []
