"""Validate the exact redistributable WeText subset before building an installer."""
import hashlib
import json
from pathlib import Path


FST_PATHS = frozenset({
    "zh/tn/tagger.fst", "zh/tn/verbalizer.fst",
    "en/tn/tagger.fst", "en/tn/verbalizer.fst",
})
NOTICE_PATHS = frozenset({"LICENSE", "NOTICE", "PROVENANCE.json"})


def bundle_files(root: Path) -> list[Path]:
    """Reject missing, changed or additional assets; return only approved files."""
    folder = root / "desktop_app/assets/wetext"
    actual = {file.relative_to(folder).as_posix()
              for file in folder.rglob("*") if file.is_file()}
    if actual != FST_PATHS | NOTICE_PATHS:
        raise ValueError("WeText bundle must contain exactly four pinned FSTs and three notices")
    manifest = json.loads((root / "packaging/wetext-manifest.json").read_text(encoding="utf-8"))
    records = manifest["files"]
    if len(records) != len(FST_PATHS) or {item["p"] for item in records} != FST_PATHS:
        raise ValueError("Unexpected WeText file manifest")
    provenance = json.loads((folder / "PROVENANCE.json").read_text(encoding="utf-8"))
    if (provenance["repo"] != manifest["repo"] or provenance["revision"] != manifest["revision"]
            or provenance["files"] != records):
        raise ValueError("WeText provenance does not match the pinned component")
    for item in records:
        file = folder / item["p"]
        if file.stat().st_size != item["s"] or hashlib.sha256(file.read_bytes()).hexdigest() != item["h"]:
            raise ValueError(f"Bundled WeText checksum mismatch: {item['p']}")
    license_hash = hashlib.sha256((folder / "LICENSE").read_bytes()).hexdigest()
    if license_hash != provenance["license_text"]["sha256"]:
        raise ValueError("Bundled WeText license checksum mismatch")
    return [folder / name for name in sorted(actual)]
