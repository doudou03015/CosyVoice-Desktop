"""Collect installed distribution notices, keeping package versions and provenance."""
import argparse
import importlib.metadata
import hashlib
import json
import re
from pathlib import Path
import shutil


def collect(packages, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for distribution in importlib.metadata.distributions(path=[str(packages)]):
        meta = distribution.metadata
        name, version = meta.get('Name', 'unknown'), meta.get('Version', 'unknown')
        records.append(dict(name=name, version=version, license=meta.get('License', ''), homepage=meta.get('Home-page', ''), project_urls=meta.get_all('Project-URL', [])))
        license_files = []
        for item in distribution.files or []:
            name_part = Path(str(item)).name
            legal = bool(re.fullmatch(r'(?:LICENSE|LICENCE|COPYING|NOTICE)(?:[._-][A-Za-z0-9-]+)*', name_part, re.IGNORECASE))
            if legal and Path(name_part).suffix.lower() not in ('.py', '.c', '.cc', '.cpp', '.h', '.png', '.svg', '.qml') and 'test' not in name_part.lower():
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    relative = str(item).replace('../', '').replace('..\\', '')
                    short = hashlib.sha256(relative.encode('utf-8')).hexdigest()[:16] + '.txt'
                    target = destination / (name.replace('/', '_') + '-' + version) / short
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    license_files.append({'archive_path': relative, 'file': target.relative_to(destination).as_posix(), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
        records[-1]['license_files'] = license_files
    (destination / 'distributions.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('packages', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    collect(args.packages, args.destination)
