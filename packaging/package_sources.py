"""Assemble source attachments and exact source provenance from downloaded archives."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4*1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', required=True, type=Path)
    args = parser.parse_args()
    source = args.stage / 'sources'
    records = json.loads((source / 'source-manifest-partial.json').read_text(encoding='utf-8'))
    if (source / 'codec-source-manifest.json').exists():
        records += json.loads((source / 'codec-source-manifest.json').read_text(encoding='utf-8'))
    if (source / 'bsd-codec-source-manifest.json').exists():
        records += json.loads((source / 'bsd-codec-source-manifest.json').read_text(encoding='utf-8-sig'))
    additions = [
        ('Qt', '6.11.2', 'qt-everywhere-src-6.11.2.tar.xz', 'https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz'),
        ('PySide6 and Shiboken6', '6.11.2', 'pyside-setup-everywhere-src-6.11.2.tar.xz', 'https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz'),
        ('Qt bundled FFmpeg', '7.1.5', 'ffmpeg-7.1.5.tar.xz', 'https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz'),
        ('Qt FFmpeg zlib', '1.3.1', 'zlib-1.3.1.tar.gz', 'https://zlib.net/fossils/zlib-1.3.1.tar.gz'),
        ('libsndfile LAME', '3.100', 'lame-3.100.tar.gz', 'https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz'),
        ('codec build recipes', 'vcpkg-2022-2024', 'codec-build-recipes.zip', 'https://github.com/microsoft/vcpkg'),
    ]
    for name, version, filename, url in additions:
        path = source / filename
        checksum = sha(path)
        official = path.with_name(path.name + '.sha256')
        if official.exists():
            assert checksum == official.read_text().split()[0].lower(), filename
        records.append(dict(name=name, version=version, file=filename, url=url, sha256=checksum))
    licenses = ROOT / 'packaging/licenses/native'
    licenses.mkdir(parents=True, exist_ok=True)
    # A streaming pass avoids unpacking source trees into the repository.
    for record in records:
        path = source / record['file']
        if zipfile.is_zipfile(path):
            continue
        group = licenses / (record['name'].replace(' ', '_') + '-' + record['version'])
        if group.exists():
            continue  # Exact source version notices already collected in an earlier pass.
        index = []
        with tarfile.open(path, 'r|*') as archive:
            for member in archive:
                base = Path(member.name).name
                if not member.isfile() or member.size > 500000:
                    continue
                parts = Path(member.name).parts
                exact = re.fullmatch(r'(?:LICENSE|LICENCE|COPYING|NOTICE)(?:[._-][A-Za-z0-9-]+)*', base, re.IGNORECASE)
                legal_folder = 'LICENSES' in parts and Path(base).suffix.lower() in ('.txt', '.md')
                excluded = any(part.lower() in ('tests', 'test', 'fixtures', 'testdata', 'examples') for part in parts)
                legal_extension = Path(base).suffix.lower() not in ('.py', '.c', '.cc', '.cpp', '.h', '.png', '.svg', '.qml')
                if (exact or legal_folder) and legal_extension and not excluded and 'test' not in base.lower():
                    # Keep archive-relative identity instead of flattening unrelated notices.
                    if '..' in parts or Path(member.name).is_absolute():
                        raise ValueError('Unsafe license archive member')
                    short = hashlib.sha256(member.name.encode('utf-8')).hexdigest()[:16] + '.txt'
                    target = group / short
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        data = archive.extractfile(member).read()
                        target.write_bytes(data)
                        index.append({'archive_path': member.name, 'file': short, 'sha256': hashlib.sha256(data).hexdigest()})
        if index:
            (group / 'license-index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {'schema_version': 1, 'sources': records,
                'qt_ffmpeg_configuration': "--prefix=/c/FFmpeg-n7.1.5/build/msvc/installed --disable-programs --disable-doc --disable-debug --enable-network --disable-lzma --enable-pic --disable-vulkan --disable-v4l2-m2m --disable-decoder=truemotion1 --disable-avdevice --disable-avfilter --enable-zlib --extra-cflags='-IC:/zlib-1.3.1/build/amd64' --extra-ldflags='-LIBPATH:C:/zlib-1.3.1/build/amd64' --toolchain=msvc --enable-shared --disable-static",
                'notes': ['Qt/PySide libraries remain dynamically replaceable in the onedir package.', 'soxr 1.1.0 source archive includes libsoxr 0.1.3-14-ga66f3ee source.', 'Runtime and GUI soundfile bundle libsndfile 1.2.0 and 1.2.2 respectively.', 'The external video FFmpeg component is downloaded directly from its upstream publisher, not rehosted in this release.', 'mpg123 source and patch identity verified against DLL embedded build paths: runtime SHA512 patchset prefix 3db975bc05 (1.29.3), GUI 66150af195 (1.32.9).']}
    (ROOT / 'packaging/source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output = args.stage / 'release/third-party-sources-0.1.0-alpha.1.zip'
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for record in records:
            archive.write(source / record['file'], record['file'])
        archive.write(ROOT / 'packaging/source-manifest.json', 'source-manifest.json')
        for notice in (ROOT / 'packaging/licenses/native/BSD-codecs').glob('*'):
            if notice.is_file():
                archive.write(notice, 'BSD-codec-notices/' + notice.name)
    print(output, output.stat().st_size, sha(output), flush=True)


if __name__ == '__main__':
    main()
