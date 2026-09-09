"""Catch failures before Qt starts, including errors importing the application."""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def entry():
    try:
        from desktop_app.main import main
        return main()
    except Exception as error:
        details = traceback.format_exc()
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Temp'
        base.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='cosyvoice-desktop-startup-', dir=str(base)))
        log = folder / 'startup-error.log'
        log.write_text(details, encoding='utf-8')
        if '--report' in sys.argv:
            try:
                report = Path(sys.argv[sys.argv.index('--report') + 1])
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps({'ok': False, 'stage': 'startup', 'error': str(error), 'traceback': details, 'log': str(log)}, ensure_ascii=False, indent=2), encoding='utf-8')
            except (OSError, IndexError):
                pass
        if sys.stderr is not None:
            sys.stderr.write(details)
        if os.name == 'nt' and not any(flag in sys.argv for flag in ('--verify-installation', '--powerpoint-render', '--report')):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'程序启动失败：{error}\n\n详细日志已保存：\n{log}', 'CosyVoice 启动提示', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(entry())
