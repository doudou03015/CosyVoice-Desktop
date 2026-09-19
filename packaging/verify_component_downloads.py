"""Exercise the production installer with isolated, optionally empty caches.

Run from the repository root with ``python -B packaging/verify_component_downloads.py``.
No application settings, existing components or user download caches are changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True,
                        help='A new directory below system Temp; retained for resume and inspection')
    parser.add_argument('--component', default='model-cosyvoice3')
    parser.add_argument('--mode', choices=['fresh', 'resume', 'reuse', 'cache-recovery'], default='fresh')
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args(argv)
    from desktop_app import __version__, downloads, paths, runtime

    system_temp = paths.outside_sync(Path(os.environ['LOCALAPPDATA']) / 'Temp')
    work = paths.outside_sync(options.work)
    if system_temp not in work.parents:
        raise ValueError('Verification work must be a unique directory below system Temp')
    marker = work / 'download-verification.json'
    if options.mode == 'fresh':
        work.mkdir(parents=True, exist_ok=False)
        marker.write_text(json.dumps({'schema_version': 1, 'component': options.component}), encoding='utf-8')
    elif not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('component') != options.component:
        raise ValueError('Resume only this tool\'s matching verification directory')
    report_path = paths.outside_sync(options.report)
    if work not in report_path.parents:
        raise ValueError('Report must be inside the verification work directory')
    paths._SESSION = work / ('session-' + options.mode)
    paths.configure_worker_environment(paths._SESSION)
    log = report_path.with_suffix('.jsonl')
    calls = []
    stages = Counter()
    original_urlopen = downloads.urlopen
    no_network = options.mode in ('reuse', 'cache-recovery')

    def request(url, *args, **kwargs):
        address = url.full_url if hasattr(url, 'full_url') else str(url)
        calls.append({'host': urlparse(address).hostname,
                      'range': url.get_header('Range') if hasattr(url, 'get_header') else None})
        if no_network:
            raise AssertionError('A verified cache or installed component must not request the network')
        return original_urlopen(url, *args, **kwargs)

    last_print = 0.0
    last_message = ''

    def progress(event):
        nonlocal last_print, last_message
        stages[event.get('stage', '')] += 1
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        now = time.monotonic()
        message = event.get('message', '')
        if message != last_message or now - last_print > 10:
            print(json.dumps(event, ensure_ascii=False), flush=True)
            last_print, last_message = now, message

    manifest = runtime.load_manifest()
    spec = manifest['components'][options.component]
    destination = work / ('recovered-components' if options.mode == 'cache-recovery' else 'components')
    started = time.monotonic()
    result = {'complete': False, 'app_version': __version__, 'mode': options.mode,
              'component': options.component, 'component_version': spec['version'],
              'file_count': len(spec['files']), 'network_disabled': no_network,
              'bundled_file_count': sum('bundled_path' in item for item in spec['files'])}
    downloads.urlopen = request
    try:
        installed = runtime.install_component(options.component, {'component_dir': str(destination)}, progress)
        result.update(complete=True, installed=installed)
    except Exception as error:
        result['error'] = str(error)
    finally:
        downloads.urlopen = original_urlopen
        result.update(elapsed_seconds=round(time.monotonic() - started, 3),
                      requests=calls, request_count=len(calls), stages=dict(stages))
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(run())
