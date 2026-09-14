"""Gather approved final artifacts and checksum every independent release asset."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
_version_tree = ast.parse((ROOT / 'desktop_app/__init__.py').read_text(encoding='utf-8'))
APP_VERSION = next((ast.literal_eval(node.value) for node in _version_tree.body
                    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__'
                                                           for target in node.targets)), None)
if not isinstance(APP_VERSION, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', APP_VERSION):
    raise ValueError('Cannot read a valid __version__ from desktop_app/__init__.py')


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''): value.update(chunk)
    return value.hexdigest()


def finalize(stage, desktop):
    output = stage / 'release'
    output.mkdir(exist_ok=True)
    for name in ['component-manifest.json', 'source-manifest.json', 'wetext-manifest.json']:
        shutil.copy2(ROOT / 'packaging' / name, output / name)
    for name in [f'CosyVoice-Desktop-{APP_VERSION}-windows-x64-setup.exe', f'CosyVoice-Desktop-{APP_VERSION}-windows-x64-portable.zip']:
        source = desktop / name
        if not source.is_file(): raise FileNotFoundError(source)
        shutil.copy2(source, output / name)
    with zipfile.ZipFile(output / f'third-party-licenses-{APP_VERSION}.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder in [ROOT / 'packaging/licenses', ROOT / 'voice_library/licenses']:
            for file in sorted(folder.rglob('*')):
                if file.is_file(): archive.write(file, file.relative_to(ROOT).as_posix())
        for runtime in ['runtime-cu121', 'runtime-cu128']:
            runtime_root = stage / runtime
            notices = runtime_root / 'THIRD_PARTY_LICENSES'
            if not notices.is_dir():
                raise FileNotFoundError(notices)
            for file in sorted(notices.rglob('*')):
                if file.is_file():
                    archive.write(file, runtime + '/' + file.relative_to(runtime_root).as_posix())
            archive.write(runtime_root / 'LICENSE.txt', runtime + '/PYTHON-LICENSE.txt')
        for file in [ROOT/'NOTICE', ROOT/'LICENSE', ROOT/'docs/licenses.md', ROOT/'docs/build-release.md', ROOT/'packaging/source-manifest.json']:
            if file.exists(): archive.write(file, file.relative_to(ROOT).as_posix())
    shutil.copy2(ROOT / 'docs/build-release.md', output / 'BUILD-RELEASE.md')
    sums = []
    for file in sorted(output.iterdir()):
        if file.is_file() and file.name != 'SHA256SUMS.txt':
            sums.append(f'{sha(file)}  {file.name}')
    (output / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--desktop', required=True, type=Path)
    args = parser.parse_args()
    print(finalize(args.stage.resolve(), args.desktop.resolve()), flush=True)
