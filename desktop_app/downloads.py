"""Verified resumable downloads and transactional component installation."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

from . import __version__
from .paths import outside_sync, session_dir


class DownloadCancelled(Exception):
    pass


def check_cancel(cancel=None):
    if cancel and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
        raise DownloadCancelled('下载已取消，已下载部分保留，可再次继续。')


def file_hash(path, cancel=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


def download_file(url, destination, sha256, size=None, progress=None, cancel=None):
    if urlparse(url).scheme != 'https':
        # Local HTTP exists only for automated download/resume tests.
        if not (os.environ.get('COSYVOICE_TEST_HTTP') == '1' and urlparse(url).hostname in ('127.0.0.1', 'localhost')):
            raise ValueError('组件下载必须使用 HTTPS。')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', sha256):
        raise ValueError('组件缺少有效 SHA256。')
    destination = outside_sync(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and (size is None or destination.stat().st_size == size) and file_hash(destination, cancel) == sha256.lower():
        return destination
    partial = destination.with_name(destination.name + '.part')
    check_cancel(cancel)
    offset = partial.stat().st_size if partial.exists() else 0
    if size is not None and offset >= size:
        if offset == size and file_hash(partial, cancel) == sha256.lower():
            os.replace(partial, destination)
            return destination
        partial.unlink()
        offset = 0
    request = Request(url, headers={'User-Agent': f'CosyVoice-Desktop/{__version__}', **({'Range': f'bytes={offset}-'} if offset else {})})
    with urlopen(request, timeout=45) as response:
        if offset and response.status == 206:
            content_range = response.headers.get('Content-Range', '')
            if not content_range.startswith(f'bytes {offset}-'):
                raise ValueError('服务器返回错误的续传位置。')
        elif offset:
            offset = 0  # Server ignored Range; overwrite instead of appending.
        total = size or int(response.headers.get('Content-Length', '0')) + offset
        with partial.open('ab' if offset else 'wb') as output:
            completed = offset
            while True:
                check_cancel(cancel)
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                completed += len(block)
                if size is not None and completed > size:
                    raise ValueError('下载文件超过清单大小。')
                if progress:
                    progress(dict(stage='download', message=destination.name, completed=completed, total=total))
            output.flush()
            os.fsync(output.fileno())
    if size is not None and partial.stat().st_size != size:
        raise ValueError('下载尚未完成，请重试续传。')
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
        elif path.stat().st_size != entry['size'] or file_hash(path, cancel) != entry['sha256']:
            errors.append(entry['path'] + ': 文件校验不匹配')
        if progress:
            progress(dict(stage='verify', message=entry['path'], completed=index + 1, total=len(files)))
    return {'valid': not errors, 'errors': errors, 'checked': len(files)}


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
            return destination
    # Stable temp cache permits continuation after app restart; extracted stages are unique.
    cache = outside_sync(session_dir().parent / 'CosyVoice-Desktop-downloads' / (identifier + '-' + spec['version']))
    cache.mkdir(parents=True, exist_ok=True)
    payload_size = sum(item['size'] for item in spec['files'])
    download_size = sum(item['size'] for item in spec.get('assets', spec['files']))
    reserve = 512 * 1024**2
    required_temp = payload_size + download_size * (2 if spec.get('assets') else 1) + reserve
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
                for index, asset in enumerate(spec['assets']):
                    part = download_file(asset['url'], cache / f'part-{index:03}', asset['sha256'], asset['size'], progress, cancel)
                    with part.open('rb') as source:
                        shutil.copyfileobj(source, output, 4 * 1024 * 1024)
            safe_extract(combined, staging, cancel)
            if spec.get('strip_prefix'):
                nested = _member_path(staging, spec['strip_prefix'])
                if not nested.is_dir():
                    raise ValueError('压缩包缺少指定的组件目录。')
                staging = nested
        else:
            for item in spec['files']:
                stored = download_file(item['url'], cache / item['sha256'], item['sha256'], item['size'], progress, cancel)
                target = _member_path(staging, item['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
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
