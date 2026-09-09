import json
from desktop_app import runtime


def test_incomplete_component_not_enabled(tmp_path, monkeypatch):
    specs = {name: {'id': name, 'version': 'v1'} for name in ('runtime-cu121','model-cosyvoice3','ffmpeg')}
    monkeypatch.setattr(runtime, 'load_manifest', lambda path=None: {'components': specs})
    settings = dict(component_dir=str(tmp_path), runtime_id='cu121')
    root = tmp_path / 'runtime-cu121-v1'
    root.mkdir()
    (root / 'python.exe').write_bytes(b'partial')
    result = runtime.resolve_components(settings)
    assert not result['ready']
    assert result['runtime_python'] == ''


def test_custom_component_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'load_manifest', lambda path=None: {'components': {}})
    python = tmp_path / 'python.exe'
    python.touch()
    ffmpeg = tmp_path / 'ffmpeg.exe'
    ffmpeg.touch()
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'cosyvoice3.yaml').touch()
    result = runtime.resolve_components(dict(runtime_python=str(python), ffmpeg_path=str(ffmpeg), model_dir=str(model), runtime_id='cu121'))
    assert result['ready']
