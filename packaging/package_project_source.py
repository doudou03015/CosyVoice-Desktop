"""Archive tracked project and submodule sources without local data or credentials."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def git(directory, *arguments):
    return subprocess.check_output([
        'git', '-c', 'safe.directory=' + directory.as_posix(),
        '-C', str(directory), *arguments,
    ])


def package(output):
    output = output.resolve()
    temporary = (Path(os.environ['LOCALAPPDATA']) / 'Temp').resolve()
    if temporary not in output.parents:
        raise ValueError('Build the source archive in a task directory inside system Temp.')
    output.parent.mkdir(parents=True, exist_ok=True)
    modules = [ROOT, ROOT / 'third_party/Matcha-TTS']
    revisions = {}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for module in modules:
            relative = module.relative_to(ROOT).as_posix()
            revisions[relative] = git(module, 'rev-parse', 'HEAD').decode().strip()
            for raw in git(module, 'ls-files', '-z').split(b'\0'):
                if not raw:
                    continue
                file = module / raw.decode('utf-8')
                if file.is_file():
                    archive.write(file, 'CosyVoice-Desktop/' + file.relative_to(ROOT).as_posix())
        archive.writestr('CosyVoice-Desktop/SOURCE-REVISIONS.json',
                         json.dumps(revisions, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    print(package(parser.parse_args().output))
