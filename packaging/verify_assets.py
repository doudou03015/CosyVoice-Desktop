"""Verify release chunks and inspect concatenated ZIPs without joining gigabytes."""
import argparse
import bisect
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
import zipfile


class PartsReader(io.RawIOBase):
    def __init__(self, parts):
        self.parts = parts
        self.ends = []
        total = 0
        for path in parts:
            total += path.stat().st_size
            self.ends.append(total)
        self.length, self.position = total, 0
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        return self.position
    def read(self, size=-1):
        if size < 0: size = self.length - self.position
        result = []
        while size > 0 and self.position < self.length:
            index = bisect.bisect_right(self.ends, self.position)
            beginning = self.ends[index - 1] if index else 0
            amount = min(size, self.ends[index] - self.position)
            with self.parts[index].open('rb') as source:
                source.seek(self.position - beginning)
                block = source.read(amount)
            result.append(block)
            self.position += len(block)
            size -= len(block)
        return b''.join(result)


def verify(manifest, assets):
    results = []
    for name, spec in manifest['components'].items():
        if not name.startswith('runtime-'): continue
        parts = []
        for asset in spec['assets']:
            path = assets / unquote(Path(urlparse(asset['url']).path).name)
            assert path.stat().st_size == asset['size'], path
            value = hashlib.sha256()
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(4*1024*1024), b''): value.update(chunk)
            assert value.hexdigest() == asset['sha256'], path
            assert asset['size'] < 2 * 1024**3, path
            parts.append(path)
        with zipfile.ZipFile(PartsReader(parts)) as archive:
            entries = {entry.filename: entry.file_size for entry in archive.infolist()}
            assert entries == {entry['path']: entry['size'] for entry in spec['files']}, name
            # Read a functional Python module and the interpreter across ZIP offsets.
            for entry in ['python.exe', 'Lib/site-packages/torch/__init__.py']:
                expected = next(item for item in spec['files'] if item['path'] == entry)
                assert hashlib.sha256(archive.read(entry)).hexdigest() == expected['sha256'], entry
        results.append({'component': name, 'parts': len(parts), 'files': len(entries), 'valid': True})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.manifest.read_text(encoding='utf-8')), args.assets)
    args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
