"""Generate comparable examples for locally imported demos using one GPU model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop_app.engine import Engine
from desktop_app.paths import atomic_json, outside_sync
from desktop_app.voices import validate_reference
from import_demo_voices import DEMO_TEXT, SAMPLES


def generate(data_dir, model_dir, stage):
    from desktop_app import paths
    stage = outside_sync(stage)
    system_temp = outside_sync(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    if system_temp not in stage.parents:
        raise ValueError("合成临时目录必须位于系统 Temp 的任务目录中。")
    paths._SESSION = stage
    stage.mkdir(parents=True, exist_ok=True)
    manifest = outside_sync(data_dir) / "voices/local-presets/manifest.json"
    catalog = json.loads(manifest.read_text(encoding="utf-8-sig"))
    selected = {item[0] for item in SAMPLES}
    engine = Engine(model_dir, stage, lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
    runtime = engine.load()
    reports = []
    for row in catalog["voices"]:
        if row["id"] not in selected:
            continue
        reference = (manifest.parent / row["reference_audio"]).resolve()
        output = manifest.parent / row["id"] / ("demo-" + uuid4().hex + ".wav")
        result = engine.synthesize(DEMO_TEXT, reference, row["transcript"], output, speed=1.0, seed=0)
        stats = validate_reference(output, DEMO_TEXT)
        row.update(demo_audio=output.relative_to(manifest.parent).as_posix(), demo_text=DEMO_TEXT,
                   inference_validation=dict(success=True, model=runtime["model"], runtime=runtime,
                                             speed=1.0, seed=0, text=DEMO_TEXT,
                                             result=result, audio=stats,
                                             assessment="实际GPU合成通过；尚待用户确认听感。"))
        atomic_json(manifest, catalog)
        report = dict(id=row["id"], name=row["name"], audio=str(output), **result)
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    atomic_json(stage / "synthesis-report.json", reports)
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.data_dir, arguments.model_dir, arguments.stage)
