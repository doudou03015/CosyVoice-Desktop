# Desktop architecture and module contract

The desktop UI is a PySide6 application and never imports torch. `desktop_app.worker`
runs under a separately versioned Python 3.10 CUDA runtime; both run from the same
application source payload. Public modules must be importable without model weights.

## Shared paths and settings

`desktop_app.paths` owns app_root(), user_data_dir(), session_dir(), load_settings(),
save_settings(dict), atomic_json(path, value), configure_worker_environment(path).
Settings keys: model_dir, runtime_python, runtime_id (cu121 or cu128), gpu_uuid,
ffmpeg_path, component_dir, manifest_path, tail_silence (default 0.5).
Development overrides: COSYVOICE_DESKTOP_ROOT, COSYVOICE_DESKTOP_DATA.

## Worker wire protocol

Start: `python -B -u -m desktop_app.worker --model-dir PATH --session-dir PATH`.
The process working directory / PYTHONPATH is the application source root.
Set CUDA_VISIBLE_DEVICES to the selected physical GPU UUID before launching.
One UTF-8 JSON object per stdin/stdout line. All third-party logs go to stderr.

Requests have `id` and `command`: load, self_test, synthesize, cancel, shutdown.
Synthesize adds: text, ref_audio (absolute), ref_text, speed (default 1.0),
seed (default 0), output_path (absolute final WAV destination).
Cancel targets the currently running synthesis; it is accepted while inference runs.

Events have `id` and `event`: progress, result, error, cancelled.
Progress includes stage, message and optional completed/total.
Result includes path, duration, sample_rate (24000), segments, and optional device/runtime
self-test details. Error includes code and message. Cancelled includes message.
Request IDs are echoed exactly. Only one synthesis is active; main thread serializes jobs.

## Backend and UI integration

Document, project, voice and media modules are plain Python and must not import Qt/torch.
Their maintainer documents exact exported functions in this file for the UI maintainer.
GPU/runtime/downloader maintainer does the same. JSON project/resource paths are relative
to their owning project, while UI/worker API paths are fully resolved.

Build/test caches and partial downloads belong to the unique system Temp task folder.
No temporary files or intermediate artifacts may be placed in E:/BaiduSyncdisk.

## Documents, projects, voices and media API

All functions accept `str | Path`. Long operations run on the UI's background task
thread. `progress(event)` receives `{stage, message, completed, total}`;
`cancel()` returns a bool and cancellation raises `InterruptedError`.

- `documents.read_text(path) -> str` reads TXT/DOCX paragraphs and tables.
  `parse_page_script(text, page_count) -> dict[int,str]` validates numbered headings.
  `read_pptx(path) -> dict` returns source, source_sha256, width, height, slides.
  A slide is `{number,title,text,hidden,included,blank_seconds,voice_id,speed,status,audio,image}`.
  Empty voice_id and null speed inherit project settings. `powerpoint_available() -> bool`.
  `render_pptx(path, output_dir, progress=None, cancel=None) -> dict[int,str]` returns
  complete PNGs copied from a unique Temp working directory. A fresh PowerPoint
  instance opens a temporary copy read-only. The frozen app dispatches
  `--powerpoint-render SOURCE TEMP` to `documents.render_cli(argv[2:])` before Qt startup.
  Helper progress/PID/errors use `TEMP/renderer-events.jsonl` rather than stdout,
  because windowed frozen executables can have sys.stdout/sys.stderr set to None.
- `voices.VoiceLibrary(root=None)` has `list()`, `get(id)`, `add(name,audio,transcript)`,
  `rename(id,name)`, `delete(id)`. Voice dicts expose id, name, absolute audio,
  transcript, kind (preset/custom/temporary), source, license, demo_audio.
  `validate_reference(audio,transcript)` returns audio stats or raises ValueError.
  `temporary_voice(audio,transcript)` validates without saving a library entry.
- `projects.create_project(pptx,directory,voice=None,speed=1.0) -> dict` immediately
  saves source and optional voice snapshot. `save_project(project,directory,voices=None)`
  accepts an optional dict of id to resolved voice, mutates paths to project-relative
  values, preserving slide object identity. `load_project(directory) -> dict` accepts
  a directory or project.json. `resolve_resource(directory,relative) -> str` validates
  containment. `settings` contains voice_id, speed, seed, tail_silence; `voices` maps
  ids to independently snapshotted voice dicts; model_id/runtime_id are top-level.
  `effective_settings(project,slide)` resolves inheritance.
  `fingerprint(text,voice,speed=1.0,seed=0,model_id='',runtime_id='')` hashes content,
  not machine paths. `remember_audio(project,slide,directory,audio,fingerprint)`
  snapshots page WAV and saves; `cached_audio(slide,directory,fingerprint)` verifies
  WAV hash and returns a resolved path or None.
- `media.export_audio(source,destination,ffmpeg='ffmpeg',progress=None,cancel=None)`
  exports WAV/MP3 and returns the final path. `build_timeline(slides,project_dir,
  tail_silence=0.5,fps=30,sample_rate=24000)` returns frame/sample-aligned pages.
  `export_video(slides,project_dir,destination,ffmpeg='ffmpeg',gpu_index=0,
  tail_silence=0.5,progress=None,cancel=None)` returns path, duration, frames, encoder,
  timeline. Continuous PCM narration and exactly counted raw frames eliminate
  per-page AAC padding; images are padded to 1920x1080. Encoding tries h264_nvenc
  on the selected physical index then h264_mf. All intermediates remain in Temp.
