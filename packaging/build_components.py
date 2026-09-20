"""Create real standalone CUDA components. Build directories must be system Temp."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def app_version() -> str:
    """Read the release version from the desktop application source."""
    module = ast.parse((ROOT / 'desktop_app' / '__init__.py').read_text(encoding='utf-8'))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == '__version__':
                    return str(ast.literal_eval(node.value))
    raise RuntimeError('desktop_app.__version__ is missing')


VERSION = app_version()
BASE_URL = f'https://github.com/doudou03015/CosyVoice-Desktop/releases/download/v{VERSION}'
MAX_ASSET = 1900 * 1024 * 1024


def component_metadata(identifier):
    if identifier.startswith('runtime-'):
        runtime_id = identifier.removeprefix('runtime-')
        old = runtime_id == 'cu121'
        return dict(runtime_id=runtime_id, platform='windows-x86_64', python_version='3.10.21',
                    torch_version='2.3.1+cu121' if old else '2.7.1+cu128',
                    torchaudio_version='2.3.1+cu121' if old else '2.7.1+cu128',
                    cuda_version='12.1' if old else '12.8',
                    compute_capabilities=['7.5', '8.6', '8.9'] if old else ['12.0'],
                    gpu_series=['RTX 20', 'RTX 30', 'RTX 40'] if old else ['RTX 50'],
                    minimum_vram_mb=7500, nominal_minimum_vram_gb=8,
                    minimum_driver_windows='527.41' if old else '570.65')
    if identifier == 'model-cosyvoice3':
        return dict(platform='windows-x86_64', runtime_ids=['cu121','cu128'],
                    compute_capabilities=['7.5','8.6','8.9','12.0'], nominal_minimum_vram_gb=8)
    return {}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def tree_manifest(folder):
    return [dict(path=p.relative_to(folder).as_posix(), size=p.stat().st_size, sha256=digest(p))
            for p in sorted(folder.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']


def bundled_wetext_files():
    """Use reviewed, pinned ModelScope resources, never infer a HF repository."""
    wetext = json.loads((ROOT / 'packaging/wetext-manifest.json').read_text(encoding='utf-8-sig'))
    files = []
    for item in wetext['files']:
        relative = 'desktop_app/assets/wetext/' + item['p']
        origin = (ROOT / relative).resolve()
        origin.relative_to((ROOT / 'desktop_app/assets/wetext').resolve())
        if origin.stat().st_size != item['s'] or digest(origin) != item['h']:
            raise ValueError('Bundled WeText checksum mismatch: ' + item['p'])
        files.append(dict(path='wetext/' + item['p'], size=item['s'], sha256=item['h'],
                          bundled_path=relative))
    return files


def ignore(directory, names):
    return [name for name in names if name in ('__pycache__', '.git', '.temp', '_virtualenv.py', '_virtualenv.pth') or name.endswith('.pyc')]


def prepare_runtime(source, destination):
    if destination.exists():
        raise ValueError('Runtime staging directory already exists: ' + str(destination))
    actual_python = (source / '.runtime/python/cpython-3.10-windows-x86_64-none').resolve()
    shutil.copytree(actual_python, destination, ignore=ignore)
    packages = source / '.venv/Lib/site-packages'
    shutil.rmtree(destination / 'Lib/site-packages', ignore_errors=True)
    shutil.copytree(packages, destination / 'Lib/site-packages', dirs_exist_ok=True, ignore=ignore)
    for directory in ['Library', 'share']:
        payload = source / '.venv' / directory
        if payload.exists():
            shutil.copytree(payload, destination / directory, dirs_exist_ok=True, ignore=ignore)
    for pth in (destination / 'Lib/site-packages').glob('*.pth'):
        value = pth.read_text(encoding='utf-8', errors='replace')
        if str(source).lower() in value.lower() or ':\\' in value:
            pth.unlink()
    # Python installation root is inferred from the relocated python.exe and Lib/os.py.
    (destination / 'pyvenv.cfg').unlink(missing_ok=True)
    scripts = destination / 'Scripts'
    if scripts.exists():
        shutil.rmtree(scripts)
    licenses = destination / 'THIRD_PARTY_LICENSES'
    licenses.mkdir(exist_ok=True)
    records = []
    for distribution in importlib.metadata.distributions(path=[str(destination / 'Lib/site-packages')]):
        meta = distribution.metadata
        name, version = meta.get('Name', 'unknown'), meta.get('Version', 'unknown')
        records.append(dict(name=name, version=version, license=meta.get('License', ''), homepage=meta.get('Home-page', ''), project_urls=meta.get_all('Project-URL', [])))
        dist_license = licenses / (name.replace('/', '_') + '-' + version)
        for item in distribution.files or []:
            if any(word in Path(str(item)).name.lower() for word in ('license', 'copying', 'notice')):
                origin = Path(distribution.locate_file(item))
                if origin.is_file():
                    target = dist_license / str(item).replace('../', '').replace('..\\', '')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origin, target)
    (licenses / 'distributions.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(actual_python / 'LICENSE.txt', licenses / 'PYTHON-LICENSE.txt')


def package(folder, identifier, output):
    files = tree_manifest(folder)
    archive = output / f'{identifier}-{VERSION}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as result:
        for item in files:
            result.write(folder / item['path'], item['path'])
    assets = []
    if archive.stat().st_size < MAX_ASSET:
        assets.append(dict(url=BASE_URL + '/' + archive.name, size=archive.stat().st_size, sha256=digest(archive)))
    else:
        with archive.open('rb') as stream:
            index = 1
            while True:
                first = stream.read(4 * 1024 * 1024)
                if not first:
                    break
                part = archive.with_name(archive.name + f'.part{index:03}')
                with part.open('wb') as target:
                    target.write(first)
                    remaining = MAX_ASSET - len(first)
                    while remaining:
                        block = stream.read(min(4 * 1024 * 1024, remaining))
                        if not block:
                            break
                        target.write(block)
                        remaining -= len(block)
                assets.append(dict(url=BASE_URL + '/' + part.name, size=part.stat().st_size, sha256=digest(part)))
                index += 1
        archive.unlink()
    return dict(id=identifier, version=VERSION, files=files, assets=assets, **component_metadata(identifier))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--prepare', choices=['cu121', 'cu128'])
    parser.add_argument('--package', choices=['cu121', 'cu128', 'ffmpeg'])
    parser.add_argument('--model', action='store_true')
    args = parser.parse_args()
    stage = args.stage.resolve()
    system_temp = (Path(os.environ['LOCALAPPDATA']) / 'Temp').resolve()
    if system_temp not in stage.parents:
        raise ValueError('Build staging must be a unique child of system Temp')
    stage.mkdir(parents=True, exist_ok=True)
    output = stage / 'release'
    output.mkdir(exist_ok=True)
    manifest_path = ROOT / 'packaging/component-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'schema_version': 1, 'app_version': VERSION, 'components': {}}
    changed_components = {}
    if args.prepare:
        prepare_runtime(args.source, stage / ('runtime-' + args.prepare))
    if args.package:
        identifier = 'ffmpeg' if args.package == 'ffmpeg' else 'runtime-' + args.package
        spec = package(stage / identifier, identifier, output)
        manifest['components'][identifier] = spec
        changed_components[identifier] = spec
    if args.model:
        original = json.loads((args.source / 'model-download-info.json').read_text(encoding='utf-8-sig'))
        folder = args.source / 'pretrained_models/Fun-CosyVoice3-0.5B'
        # A previously installed model can already contain the bundled FSTs.
        items = [item for item in tree_manifest(folder)
                 if not item['path'].startswith('wetext/') and item['path'] != '.complete.json']
        for item in items:
            item['url'] = f"https://huggingface.co/{original['repo_id']}/resolve/{original['revision']}/{item['path']}"
        manifest['components']['model-cosyvoice3'] = dict(id='model-cosyvoice3', version=original['revision'], source=original['repo_id'], files=items, license='Apache-2.0', **component_metadata('model-cosyvoice3'))
        items.extend(bundled_wetext_files())
        changed_components['model-cosyvoice3'] = manifest['components']['model-cosyvoice3']
    if manifest_path.exists():
        latest = json.loads(manifest_path.read_text(encoding='utf-8'))
        latest['components'].update(changed_components)
        manifest = latest
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
