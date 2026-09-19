# PyInstaller onedir build; heavy CUDA libraries live in independently selected runtimes.
from pathlib import Path
import ast
import json
import PySide6
import os
import sys
import runpy
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo

root = Path(SPECPATH).parent
version_tree = ast.parse((root / 'desktop_app/__init__.py').read_text(encoding='utf-8'))
app_version = next(ast.literal_eval(node.value) for node in version_tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
file_version = tuple(int(part) for part in app_version.split('-')[0].split('+')[0].split('.')) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=file_version, prodvers=file_version, mask=0x3f, flags=0,
                     OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('ProductName', 'CosyVoice 配音工作台'),
        StringStruct('FileDescription', 'CosyVoice 配音工作台'),
        StringStruct('ProductVersion', app_version), StringStruct('FileVersion', app_version),
        StringStruct('InternalName', 'CosyVoice-Desktop'),
        StringStruct('OriginalFilename', 'CosyVoice-Desktop.exe'),
        StringStruct('LegalCopyright', 'See LICENSE and NOTICE'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
qt_root = Path(PySide6.__file__).parent
# Build dependency discovery must not inherit unrelated imaging/tool DLLs.
windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
os.environ['PATH'] = os.pathsep.join(str(path) for path in [Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs', qt_root, windows / 'System32', windows])
datas = []
# Fail closed if a pinned FST changes or an unrelated file enters this bundle.
wetext_tools = runpy.run_path(str(root / 'packaging/bundled_wetext.py'))
wetext_files = wetext_tools['bundle_files'](root)
wetext_root = root / 'desktop_app/assets/wetext'
datas += [(str(file), str(file.parent.relative_to(root))) for file in wetext_files]
for directory in ['desktop_app', 'cosyvoice', 'third_party/Matcha-TTS', 'packaging/licenses', 'docs', 'templates']:
    folder = root / directory
    if folder.exists():
        for file in folder.rglob('*'):
            if wetext_root in file.parents:
                continue  # Added above from the validated, exact resource list.
            if file.is_file() and '__pycache__' not in file.parts and '.git' not in file.parts and file.suffix != '.pyc':
                datas.append((str(file), str(file.parent.relative_to(root))))
voice_root = root / 'voice_library'
voices = json.loads((voice_root / 'manifest.json').read_text(encoding='utf-8'))
voice_paths = {'manifest.json'}
for voice in voices['voices']:
    voice_paths.update([voice['reference_audio'], voice['reference_transcript']])
for file in voice_root.rglob('*'):
    if file.is_file() and (file.relative_to(voice_root).as_posix() in voice_paths or 'licenses' in file.parts or file.suffix.lower() in ('.md', '.json')):
        datas.append((str(file), str(file.parent.relative_to(root))))
for name in ['zero_shot_prompt.wav']:
    datas.append((str(root / 'asset' / name), 'asset'))
for name in ['component-manifest.json', 'wetext-manifest.json', 'source-manifest.json']:
    datas.append((str(root / 'packaging' / name), 'packaging'))
for name in ['LICENSE', 'NOTICE', 'README-DESKTOP.md']:
    if (root / name).exists():
        datas.append((str(root / name), '.'))
datas += collect_data_files('docx') + collect_data_files('pptx')
a = Analysis([str(root / 'packaging/desktop_entry.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=['win32com.client', 'pythoncom', 'pywintypes'],
    excludes=['torch','torchaudio','transformers','gradio','matplotlib','tkinter','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets'], noarchive=False)
# Qt on Windows links the OS's unversioned ICU API. Never gather an unrelated
# ICU from tools (for example Poppler) discovered through a developer's PATH.
a.binaries = [entry for entry in a.binaries if not Path(entry[0]).name.lower().startswith(('icuuc', 'icuin', 'icudt'))]
# Use the coherent redistributable family shipped with the selected Qt wheel.
crt_files = [p for p in qt_root.glob('*.dll') if p.name.lower().startswith(('vcruntime140', 'msvcp140', 'concrt140'))]
crt_names = {p.name.lower() for p in crt_files}
a.binaries = [entry for entry in a.binaries if not ('/' not in entry[0].replace('\\','/') and entry[0].lower() in crt_names)]
a.binaries += [(p.name, str(p), 'BINARY') for p in crt_files]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CosyVoice-Desktop',
    version=version_info,
    icon=str(root / 'desktop_app/assets/app.ico'), debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CosyVoice-Desktop')
