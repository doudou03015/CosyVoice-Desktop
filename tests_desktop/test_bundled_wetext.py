"""The redistributable resource subset must remain pinned and self-contained."""
import json
from pathlib import Path
import runpy
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = runpy.run_path(str(ROOT / "packaging/bundled_wetext.py"))
BUNDLE_FILES = TOOLS["bundle_files"]


@pytest.fixture
def staged_bundle(tmp_path):
    shutil.copytree(ROOT / "desktop_app/assets/wetext", tmp_path / "desktop_app/assets/wetext")
    (tmp_path / "packaging").mkdir()
    shutil.copy2(ROOT / "packaging/wetext-manifest.json", tmp_path / "packaging/wetext-manifest.json")
    return tmp_path


def test_shipped_subset_matches_existing_model_identity():
    files = BUNDLE_FILES(ROOT)
    assert len(files) == 7
    assert sum(file.stat().st_size for file in files if file.suffix == ".fst") == 11_369_544
    provenance_path = ROOT / "desktop_app/assets/wetext/PROVENANCE.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "packaging/component-manifest.json").read_text(encoding="utf-8"))
    model = manifest["components"]["model-cosyvoice3"]
    for resource in provenance["files"]:
        packaged = next(item for item in model["files"] if item["path"] == "wetext/" + resource["p"])
        assert (packaged["size"], packaged["sha256"]) == (resource["s"], resource["h"])
    assert provenance["revision"] == "67928a15fac1e769e23189c53648a65dea206a39"
    assert provenance["repository_license_evidence"]["response_fields"]["Data.License"] == "Apache License 2.0"
    text = provenance_path.read_text(encoding="utf-8").lower()
    assert "c:\\" not in text and "e:\\" not in text and "administrator" not in text


def test_manifest_generator_keeps_bundled_sources_and_cache_identity():
    generator = runpy.run_path(str(ROOT / "packaging/build_components.py"))
    generated = generator["bundled_wetext_files"]()
    manifest = json.loads((ROOT / "packaging/component-manifest.json").read_text(encoding="utf-8"))
    model = manifest["components"]["model-cosyvoice3"]
    assert model["version"] == "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
    assert generated == [item for item in model["files"] if item["path"].startswith("wetext/")]
    assert len(generated) == 4 and all("url" not in item for item in generated)


def test_build_rejects_corrupted_resource(staged_bundle):
    file = staged_bundle / "desktop_app/assets/wetext/en/tn/verbalizer.fst"
    data = bytearray(file.read_bytes())
    data[-1] ^= 1
    file.write_bytes(data)
    with pytest.raises(ValueError, match="checksum mismatch"):
        BUNDLE_FILES(staged_bundle)


@pytest.mark.parametrize("extra", ["zh/itn/tagger.fst", "local-settings.json"])
def test_build_rejects_resources_outside_exact_allowlist(staged_bundle, extra):
    file = staged_bundle / "desktop_app/assets/wetext" / extra
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"not part of the approved bundle")
    with pytest.raises(ValueError, match="exactly four pinned"):
        BUNDLE_FILES(staged_bundle)


def test_build_rejects_missing_attribution(staged_bundle):
    (staged_bundle / "desktop_app/assets/wetext/NOTICE").unlink()
    with pytest.raises(ValueError, match="exactly four pinned"):
        BUNDLE_FILES(staged_bundle)


def test_build_rejects_changed_provenance(staged_bundle):
    file = staged_bundle / "desktop_app/assets/wetext/PROVENANCE.json"
    data = json.loads(file.read_text(encoding="utf-8"))
    data["files"][0]["h"] = "0" * 64
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        BUNDLE_FILES(staged_bundle)
