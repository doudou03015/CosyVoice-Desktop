"""Resolve and install independently versioned desktop components."""
from __future__ import annotations
import json
from pathlib import Path
from .paths import app_root, load_settings
from .downloads import install_package, validate_files


def load_manifest(path=None):
    source = Path(path) if path else app_root() / 'packaging' / 'component-manifest.json'
    manifest = json.loads(source.read_text(encoding='utf-8-sig'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('components'), dict):
        raise ValueError('组件清单格式不支持。')
    return manifest


def resolve_components(settings=None):
    settings = settings or load_settings()
    manifest = load_manifest(settings.get('manifest_path') or None)
    runtime_id = settings.get('runtime_id') or 'cu121'
    result = {'runtime_id': runtime_id, 'missing': []}
    for key, identifier, relative in [('runtime_python', 'runtime-' + runtime_id, 'python.exe'), ('model_dir', 'model-cosyvoice3', ''), ('ffmpeg_path', 'ffmpeg', 'bin/ffmpeg.exe')]:
        configured = settings.get(key)
        spec = manifest['components'].get(identifier)
        if configured:
            candidate = Path(configured).expanduser().resolve()
        elif spec:
            base = Path(settings['component_dir']) / (identifier + '-' + spec['version'])
            candidate = base / relative
            if not (base / '.complete.json').is_file():
                result[key] = ''
                result['missing'].append(identifier)
                continue
        else:
            result[key] = ''
            result['missing'].append(identifier)
            continue
        valid = (candidate / 'cosyvoice3.yaml').is_file() if key == 'model_dir' else candidate.is_file()
        result[key] = str(candidate) if valid else ''
        if not valid:
            result['missing'].append(identifier)
    result['ready'] = not result['missing']
    return result


def validate_model(path, progress=None, cancel=None):
    manifest = load_manifest()
    files = manifest['components']['model-cosyvoice3']['files']
    regular = [item for item in files if not item['path'].startswith('wetext/')]
    texts = [item for item in files if item['path'].startswith('wetext/')]
    result = validate_files(path, regular, progress, cancel)
    if texts:
        wetext = Path(path) / 'wetext'
        if not wetext.is_dir():
            wetext = Path(path).parent / 'wetext'
        if not wetext.is_dir():
            wetext = app_root() / 'pretrained_models/wetext'
        adapted = [dict(item, path=item['path'].removeprefix('wetext/')) for item in texts]
        text_result = validate_files(wetext, adapted, progress, cancel)
        result['errors'] += text_result['errors']
        result['checked'] += text_result['checked']
        result['valid'] = not result['errors']
    return result


def install_component(component_id, settings=None, progress=None, cancel=None):
    settings = settings or load_settings()
    manifest = load_manifest(settings.get('manifest_path') or None)
    spec = manifest['components'].get(component_id)
    if not spec:
        raise ValueError('发布包不包含此组件：' + component_id)
    path = install_package(spec, settings['component_dir'], progress, cancel)
    result = {'id': component_id, 'path': str(path)}
    if component_id.startswith('runtime-'):
        result.update(runtime_python=str(path / 'python.exe'), runtime_id=component_id.removeprefix('runtime-'))
    elif component_id == 'model-cosyvoice3':
        result['model_dir'] = str(path)
    elif component_id == 'ffmpeg':
        result['ffmpeg_path'] = str(path / 'bin' / 'ffmpeg.exe')
    return result
