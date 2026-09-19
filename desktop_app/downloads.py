"""Verified resumable downloads and transactional component installation."""
from __future__ import annotations
import hashlib
from http.client import IncompleteRead, RemoteDisconnected
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
import zipfile

from . import __version__
from .paths import app_root, outside_sync, session_dir


NETWORK_TIMEOUT = 15
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAYS = (0.5, 1.0)


class DownloadCancelled(Exception):
    pass


class DownloadError(RuntimeError):
    pass


class _IncompleteDownload(Exception):
    pass


def check_cancel(cancel=None):
    if cancel and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
        raise DownloadCancelled('下载已取消，已下载部分保留，可再次继续。')


def file_hash(path, cancel=None):
    check_cancel(cancel)
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


def _report(progress, stage, message, completed=0, total=0):
    if progress:
        progress(dict(stage=stage, message=message, completed=completed, total=total))


def _valid_cached_file(path, sha256, size, progress=None, cancel=None):
    check_cancel(cancel)
    if not path.is_file() or (size is not None and path.stat().st_size != size):
        return False
    _report(progress, 'verify_cache', '正在校验已下载缓存', 0, size or path.stat().st_size)
    if file_hash(path, cancel) != sha256.lower():
        return False
    _report(progress, 'cache', '已下载缓存校验通过，直接复用', path.stat().st_size, size or path.stat().st_size)
    return True


def _validate_digest(sha256, size):
    if not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha256):
        raise ValueError('组件缺少有效 SHA256。')
    if size is not None and (type(size) is not int or size < 0):
        raise ValueError('组件文件大小不合法。')


def _wait_before_retry(attempt, cancel):
    deadline = time.monotonic() + RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
    while time.monotonic() < deadline:
        check_cancel(cancel)
        delay = min(0.1, max(0, deadline - time.monotonic()))
        if hasattr(cancel, 'wait'):
            cancel.wait(delay)
        else:
            time.sleep(delay)
    check_cancel(cancel)


def _receive_file(url, partial, size, progress, cancel):
    offset = partial.stat().st_size if partial.exists() else 0
    request = Request(url, headers={'User-Agent': f'CosyVoice-Desktop/{__version__}',
                                  **({'Range': f'bytes={offset}-'} if offset else {})})
    host = urlparse(url).hostname or '下载服务器'
    _report(progress, 'connect', f'开始连接 {host}' + (f'，从已下载 {offset} 字节继续' if offset else ''), offset, size or 0)
    check_cancel(cancel)
    with urlopen(request, timeout=NETWORK_TIMEOUT) as response:
        check_cancel(cancel)
        response_size = None
        if response.status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)', response.headers.get('Content-Range', ''))
            if (not match or int(match[1]) != offset or int(match[2]) < offset
                    or (match[3] != '*' and (int(match[2]) >= int(match[3])
                        or (size is not None and int(match[3]) != size)))):
                raise ValueError('服务器返回错误的续传位置，已下载部分保留。')
            response_size = int(match[2]) - offset + 1
        elif response.status == 200:
            offset = 0  # Server ignored Range; replace the partial, never append.
        else:
            raise ValueError(f'服务器返回不支持的响应状态 HTTP {response.status}，已下载部分保留。')
        header_length = response.headers.get('Content-Length')
        if header_length is not None:
            if not header_length.isdecimal():
                raise ValueError('服务器返回无效的文件大小，已下载部分保留。')
            declared = int(header_length)
            if response_size is not None and declared != response_size:
                raise ValueError('服务器返回的续传大小不一致，已下载部分保留。')
            response_size = declared
        total = size if size is not None else (offset + (response_size or 0))
        if size is not None and response_size is not None and offset + response_size > size:
            raise ValueError('服务器返回的文件超过清单大小，已下载部分保留。')
        with partial.open('ab' if offset else 'wb') as output:
            completed = offset
            last_progress = None
            while True:
                check_cancel(cancel)
                read_error = None
                try:
                    block = response.read1(256 * 1024)
                except IncompleteRead as error:
                    block, read_error = error.partial, error
                if block:
                    if size is not None and completed + len(block) > size:
                        raise ValueError('下载文件超过清单大小，已下载部分保留。')
                    output.write(block)
                    completed += len(block)
                    now = time.monotonic()
                    # TLS read1() often yields 16 KiB records. Bound UI signal
                    # frequency while retaining cancellation on every block.
                    if last_progress is None or completed == total or now - last_progress >= 0.1:
                        _report(progress, 'download', '正在下载', completed, total)
                        last_progress = now
                if read_error is not None:
                    raise read_error
                if not block:
                    break
            output.flush()
            os.fsync(output.fileno())
    if (size is not None and completed != size) or (response_size is not None and completed - offset != response_size):
        raise _IncompleteDownload('下载响应不完整')


def download_file(url, destination, sha256, size=None, progress=None, cancel=None):
    check_cancel(cancel)
    _validate_digest(sha256, size)
    destination = outside_sync(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _valid_cached_file(destination, sha256, size, progress, cancel):
        return destination
    partial = destination.with_name(destination.name + '.part')
    if partial.is_file() and (size is None or partial.stat().st_size == size):
        if _valid_cached_file(partial, sha256, size, progress, cancel):
            os.replace(partial, destination)
            return destination
    if size is not None and partial.is_file() and partial.stat().st_size >= size:
        partial.unlink()  # Only an invalid/oversized partial is discarded.
    if urlparse(url).scheme != 'https':
        # Local HTTP exists only for automated download/resume tests.
        if not (os.environ.get('COSYVOICE_TEST_HTTP') == '1' and urlparse(url).hostname in ('127.0.0.1', 'localhost')):
            raise ValueError('组件下载必须使用 HTTPS。')
    filename = unquote(PurePosixPath(urlparse(url).path).name) or destination.name
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            _receive_file(url, partial, size, progress, cancel)
            break
        except (HTTPError, URLError, TimeoutError, ConnectionError, IncompleteRead, RemoteDisconnected, _IncompleteDownload) as error:
            check_cancel(cancel)
            host = urlparse(getattr(error, 'url', url)).hostname or '下载服务器'
            if isinstance(error, HTTPError):
                retry = error.code in (408, 429) or 500 <= error.code <= 599
                reason = f'HTTP {error.code}'
                error.close()
            else:
                retry = True
                reason = '下载响应不完整' if isinstance(error, (_IncompleteDownload, IncompleteRead)) else '网络超时或连接中断'
            # A missing chunked terminator can fail after all file bytes arrived.
            # Verify them before issuing an out-of-range Range=size request.
            if retry and size is not None and partial.is_file() and partial.stat().st_size == size:
                if _valid_cached_file(partial, sha256, size, progress, cancel):
                    os.replace(partial, destination)
                    return destination
                partial.unlink()  # Full-sized but invalid bytes cannot be resumed.
            if not retry or attempt == MAX_DOWNLOAD_ATTEMPTS:
                raise DownloadError(f'下载 {filename} 失败（{host}，{reason}）。已校验缓存和已下载部分保留，重试可继续下载。') from None
            _report(progress, 'retry', f'{host}：{reason}，准备第 {attempt + 1}/{MAX_DOWNLOAD_ATTEMPTS} 次连接；已下载部分保留',
                    partial.stat().st_size if partial.exists() else 0, size or 0)
            _wait_before_retry(attempt, cancel)
    check_cancel(cancel)
    _report(progress, 'verify_download', '下载完成，正在校验 SHA256', partial.stat().st_size, size or partial.stat().st_size)
    if file_hash(partial, cancel) != sha256.lower():
        partial.unlink(missing_ok=True)
        raise ValueError('SHA256 校验失败，请重新下载。')
    os.replace(partial, destination)
    return destination


def _member_path(root: Path, name: str) -> Path:
    name = name.replace('\\', '/')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or ':' in name or not relative.parts:
        raise ValueError('压缩包包含不安全路径。')
    destination = (root / Path(*relative.parts)).resolve()
    if root != destination and root not in destination.parents:
        raise ValueError('压缩包路径越界。')
    return destination


def safe_extract(archive, destination, cancel=None, max_bytes=30 * 1024**3):
    destination = outside_sync(destination)
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                check_cancel(cancel)
                target = _member_path(destination, member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError('不接受包含符号链接的组件。')
                total += member.file_size
                if total > max_bytes:
                    raise ValueError('解压大小超出限制。')
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
    else:
        with tarfile.open(archive, 'r:*') as package:
            for member in package:
                check_cancel(cancel)
                target = _member_path(destination, member.name)
                if not (member.isfile() or member.isdir()):
                    raise ValueError('不接受链接或特殊设备文件。')
                total += member.size
                if total > max_bytes:
                    raise ValueError('解压大小超出限制。')
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
    return destination


def validate_files(root, files, progress=None, cancel=None):
    root = Path(root).resolve()
    errors = []
    for index, entry in enumerate(files):
        check_cancel(cancel)
        path = _member_path(root, entry['path'])
        if not path.is_file():
            errors.append(entry['path'] + ': 文件缺失')
        elif path.stat().st_size != entry['size'] or file_hash(path, cancel) != entry['sha256'].lower():
            errors.append(entry['path'] + ': 文件校验不匹配')
        if progress:
            progress(dict(stage='verify', message=entry['path'], completed=index + 1, total=len(files)))
    return {'valid': not errors, 'errors': errors, 'checked': len(files)}


def _file_progress(progress, name):
    if progress is None:
        return None
    def report(event):
        progress(dict(event, file=name, message=f'{name} · {event["message"]}'))
    return report


def _bundled_file(entry, progress=None, cancel=None):
    name = entry['path']
    bundled = entry.get('bundled_path')
    if not isinstance(bundled, str) or not bundled:
        raise ValueError(f'{name}：内置资源路径无效，请修复或重新安装桌面程序。')
    root = app_root().resolve()
    try:
        source = _member_path(root, bundled)
        unresolved = root / Path(*PurePosixPath(bundled.replace('\\', '/')).parts)
        for item in (unresolved, *unresolved.parents):
            if item == root:
                break
            if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                raise ValueError('内置资源不能使用链接')
        if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
            raise ValueError('内置资源文件缺失或不是普通文件')
        _report(progress, 'verify_bundled', '正在校验程序内置资源', 0, entry['size'])
        if source.stat().st_size != entry['size'] or file_hash(source, cancel) != entry['sha256'].lower():
            raise ValueError('内置资源大小或 SHA256 校验不匹配')
    except (OSError, ValueError) as error:
        raise ValueError(f'{name}：{error}。请修复或重新安装桌面程序；已下载缓存保留，未改用网络地址。') from None
    _report(progress, 'bundled', '内置资源校验通过，直接复用', entry['size'], entry['size'])
    return source


def install_package(spec, component_dir, progress=None, cancel=None):
    component_dir = outside_sync(component_dir)
    component_dir.mkdir(parents=True, exist_ok=True)
    identifier = spec['id']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', identifier) or not re.fullmatch(r'[A-Za-z0-9_.-]+', spec['version']):
        raise ValueError('组件标识不合法。')
    destination = component_dir / (identifier + '-' + spec['version'])
    if destination.resolve().parent != component_dir.resolve():
        raise ValueError('组件目标目录不能是外部链接。')
    if destination.exists():
        validation = validate_files(destination, spec['files'], progress, cancel)
        if validation['valid']:
            (destination / '.complete.json').write_text(json.dumps({'id': identifier, 'version': spec['version']}), encoding='utf-8')
            _report(progress, 'cache', f'{identifier}：已安装组件校验通过，直接复用', len(spec['files']), len(spec['files']))
            return destination
    # Stable temp cache permits continuation after app restart; extracted stages are unique.
    cache = outside_sync(session_dir().parent / 'CosyVoice-Desktop-downloads' / (identifier + '-' + spec['version']))
    cache.mkdir(parents=True, exist_ok=True)
    sources = spec.get('assets') or spec['files']
    prepared = {}
    transfers = []
    for index, entry in enumerate(sources):
        check_cancel(cancel)
        _validate_digest(entry['sha256'], entry['size'])
        name = entry.get('path') or unquote(PurePosixPath(urlparse(entry.get('url', '')).path).name) or f'{identifier} 分片 {index + 1}'
        report = _file_progress(progress, name)
        stored = cache / (f'part-{index:03}' if spec.get('assets') else entry['sha256'])
        transfers.append((entry, stored, name, report))
        # Verify once before calculating required free space; reusing this map
        # avoids hashing several gigabytes again in download_file.
        if _valid_cached_file(stored, entry['sha256'], entry['size'], report, cancel):
            prepared[index] = stored
        elif _valid_cached_file(stored.with_name(stored.name + '.part'), entry['sha256'], entry['size'], report, cancel):
            os.replace(stored.with_name(stored.name + '.part'), stored)
            prepared[index] = stored
        elif not spec.get('assets') and 'bundled_path' in entry:
            prepared[index] = _bundled_file(entry, report, cancel)
    payload_size = sum(item['size'] for item in spec['files'])
    download_size = sum(entry['size'] for index, entry in enumerate(sources) if index not in prepared)
    archive_size = sum(entry['size'] for entry in sources) if spec.get('assets') else 0
    reserve = 512 * 1024**2
    required_temp = payload_size + download_size + archive_size + reserve
    required_target = payload_size + reserve
    same_volume = cache.anchor.lower() == component_dir.anchor.lower()
    if same_volume:
        required_temp += required_target
    if progress:
        progress(dict(stage='space_check', message=f'组件约 {payload_size / 1024**3:.1f} GB；临时空间需约 {required_temp / 1024**3:.1f} GB。', completed=0, total=payload_size))
    if shutil.disk_usage(cache).free < required_temp or (not same_volume and shutil.disk_usage(component_dir).free < required_target):
        raise OSError('磁盘空间不足，请清理系统临时目录所在磁盘和组件安装磁盘后重试。')
    with tempfile.TemporaryDirectory(prefix='install-', dir=session_dir()) as temporary:
        staging = Path(temporary) / 'payload'
        staging.mkdir()
        if spec.get('assets'):
            combined = Path(temporary) / 'package.zip'
            with combined.open('wb') as output:
                for index, (asset, stored, name, report) in enumerate(transfers):
                    check_cancel(cancel)
                    try:
                        part = prepared[index] if index in prepared else download_file(asset['url'], stored, asset['sha256'], asset['size'], report, cancel)
                    except DownloadCancelled:
                        raise
                    except Exception as error:
                        raise DownloadError(f'{name}：{error}') from None
                    with part.open('rb') as source:
                        shutil.copyfileobj(source, output, 4 * 1024 * 1024)
            safe_extract(combined, staging, cancel)
            if spec.get('strip_prefix'):
                nested = _member_path(staging, spec['strip_prefix'])
                if not nested.is_dir():
                    raise ValueError('压缩包缺少指定的组件目录。')
                staging = nested
        else:
            for index, (item, stored, name, report) in enumerate(transfers):
                check_cancel(cancel)
                try:
                    stored = prepared[index] if index in prepared else download_file(item['url'], stored, item['sha256'], item['size'], report, cancel)
                except DownloadCancelled:
                    raise
                except Exception as error:
                    raise DownloadError(f'{name}：{error}') from None
                target = _member_path(staging, item['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                _report(report, 'stage', '正在准备已校验文件', item['size'], item['size'])
                shutil.copy2(stored, target)
        result = validate_files(staging, spec['files'], progress, cancel)
        if not result['valid']:
            raise ValueError('\n'.join(result['errors']))
        check_cancel(cancel)
        # Destination receives only verified files. Marker appears only after complete copy.
        backup = Path(temporary) / 'previous-component'
        if destination.exists():
            shutil.move(str(destination), str(backup))
        try:
            shutil.copytree(staging, destination)
            (destination / '.complete.json').write_text(json.dumps({'id': identifier, 'version': spec['version']}), encoding='utf-8')
        except BaseException:
            if destination.exists():
                shutil.rmtree(destination)
            if backup.exists():
                shutil.move(str(backup), str(destination))
            raise
    return destination
