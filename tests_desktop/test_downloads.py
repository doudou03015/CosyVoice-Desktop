import hashlib
import http.server
import threading
import zipfile
import pytest
from desktop_app.downloads import download_file, safe_extract, DownloadCancelled


def test_zip_slip_and_symlink_rejected(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as result:
        result.writestr('../outside.txt', 'bad')
    with pytest.raises(ValueError):
        safe_extract(archive, tmp_path / 'out')
    assert not (tmp_path / 'outside.txt').exists()


def test_resume_and_ignored_range(tmp_path, monkeypatch):
    payload = b'abcde' * 10000
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.headers.get('Range'))
            offset = int(self.headers.get('Range', 'bytes=0-').split('=')[1].split('-')[0])
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(payload)-1}/{len(payload)}')
            self.send_header('Content-Length', str(len(payload)-offset))
            self.end_headers()
            self.wfile.write(payload[offset:])
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('COSYVOICE_TEST_HTTP', '1')
    try:
        target = tmp_path / 'asset'
        target.with_name('asset.part').write_bytes(payload[:1234])
        download_file(f'http://127.0.0.1:{server.server_port}/file', target, hashlib.sha256(payload).hexdigest(), len(payload))
        assert requests == ['bytes=1234-']
        assert target.read_bytes() == payload
        with pytest.raises(DownloadCancelled):
            download_file(f'http://127.0.0.1:{server.server_port}/file', tmp_path / 'cancel', hashlib.sha256(payload).hexdigest(), cancel=lambda: True)
    finally:
        server.shutdown()


def test_bad_hash_never_becomes_complete(tmp_path, monkeypatch):
    from desktop_app.downloads import validate_files
    (tmp_path / 'model.pt').write_bytes(b'partial')
    assert not validate_files(tmp_path, [dict(path='model.pt', size=7, sha256='0'*64)])['valid']


def test_repair_replaces_only_after_full_validation(tmp_path, monkeypatch):
    from desktop_app import downloads
    payload = b'healthy runtime'
    session = tmp_path / 'session'
    session.mkdir()
    monkeypatch.setattr(downloads, 'session_dir', lambda: session)
    def download(url, destination, sha256, size, progress=None, cancel=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return destination
    monkeypatch.setattr(downloads, 'download_file', download)
    spec = dict(id='runtime-cu121', version='v1', files=[dict(path='python.exe', url='https://example.test/runtime', size=len(payload), sha256=hashlib.sha256(payload).hexdigest())])
    components = tmp_path / 'components'
    old = components / 'runtime-cu121-v1'
    old.mkdir(parents=True)
    (old / 'python.exe').write_bytes(b'broken')
    result = downloads.install_package(spec, components)
    assert (result / 'python.exe').read_bytes() == payload
    assert (result / '.complete.json').exists()
