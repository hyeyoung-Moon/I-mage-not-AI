"""Record independent CLI probes against the current checkout."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent


def save_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def manifest(directory: Path, image: Image.Image, features: list, **extra) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    image.save(directory / "asset.png")
    digest = hashlib.sha256((directory / "asset.png").read_bytes()).hexdigest()
    data = {"schema_version": "0.5", "asset": {"path": "asset.png", "sha256": digest}, "features": features, **extra}
    path = directory / "manifest.json"
    save_json(path, data)
    return path


def cli(directory: Path, label: str, *args: object) -> dict:
    command = [sys.executable, "-m", "i_mage_not_ai", *map(str, args)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    (directory / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (directory / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    data = json.loads(result.stdout) if result.stdout else None
    strict_json = None
    if result.stdout:
        try:
            json.loads(result.stdout, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
            strict_json = True
        except ValueError:
            strict_json = False
    return {"exit": result.returncode, "strict_json": strict_json, "data": data, "stderr": result.stderr}


def slim_result(result: dict) -> dict:
    data = result["data"] or {}
    rows = [{k: row.get(k) for k in ("rule_id", "feature_id", "id", "status", "measured", "threshold", "constraint_origin") if k in row}
            for row in data.get("results", []) if row.get("algorithm") != "image_metadata"]
    return {"exit": result["exit"], "strict_json": result["strict_json"], "status": data.get("status"),
            "completeness": data.get("completeness"), "rows": rows, "stderr": result["stderr"]}


summary = {}

# Empty feature mask with ordinary positive thresholds.
d = OUT / "empty-mask"
p = manifest(d, Image.new("RGBA", (4, 4), (128, 128, 128, 255)), [{"id": "body", "role": "foreground",
    "geometry": {"type": "mask", "path": "mask.png"}, "properties": {"max_palette_colors": 1,
    "max_luma_levels": 1, "min_symmetry_iou": 0.9, "max_connected_components": 1}}])
Image.new("L", (4, 4), 0).save(d / "mask.png")
summary["empty_mask"] = slim_result(cli(d, "review", "review", "--manifest", p))

# Same luminance formula, but transparent RGB is handled differently by each path.
d = OUT / "transparent-pixels"
im = Image.new("RGBA", (2, 1))
im.putdata([(128, 128, 128, 255), (255, 255, 255, 0)])
feature = {"id": "body", "role": "foreground", "geometry": {"type": "mask", "path": "mask.png"},
           "properties": {"max_luma_levels": 1, "luma_bin_width": 64}}
p = manifest(d, im, [feature])
Image.new("L", (2, 1), 255).save(d / "mask.png")
review = cli(d, "review", "review", "--manifest", p)
measure = cli(d, "measure", "measure", "--manifest", p)
summary["transparent_luma"] = {
    "review": slim_result(review), "measure_exit": measure["exit"],
    "diagnostic_luma": measure["data"]["features"][0]["metrics"]["luma_levels"]}

# Inline and project overlap assertions for the same pair must keep their own thresholds.
d = OUT / "overlap-two-constraints"
features = [{"id": x, "role": "foreground", "geometry": {"type": "mask", "path": f"{x}.png"}} for x in ("a", "b")]
p = manifest(d, Image.new("RGBA", (4, 4), (120, 120, 120, 255)), features,
             overlap_constraints=[{"feature_a": "a", "feature_b": "b", "max_overlap_ratio": 0}])
for x in ("a", "b"):
    Image.new("L", (4, 4), 255).save(d / f"{x}.png")
rules = d / "rules.yaml"
rules.write_text("schema_version: '1'\nrules:\n  - id: PROJECT-OVERLAP\n    version: '1'\n    algorithm: {id: mask_intersection_ratio, parameters: {features: [a, b]}}\n    assertion: {operator: less_than_or_equal, value: 1, origin: project_config}\n", encoding="utf-8")
summary["overlap_constraints"] = slim_result(cli(d, "review", "review", "--manifest", p, "--rules-file", rules))

# Reversing the thresholds exposes a PASS on the strict project finding.
d = OUT / "overlap-strict-project"
features = [{"id": x, "role": "foreground", "geometry": {"type": "mask", "path": f"{x}.png"}} for x in ("a", "b")]
p = manifest(d, Image.new("RGBA", (4, 4), (120, 120, 120, 255)), features,
             overlap_constraints=[{"feature_a": "a", "feature_b": "b", "max_overlap_ratio": 1}])
for x in ("a", "b"):
    Image.new("L", (4, 4), 255).save(d / f"{x}.png")
rules = d / "rules.yaml"
rules.write_text("schema_version: '1'\nrules:\n  - id: PROJECT-OVERLAP\n    version: '1'\n    algorithm: {id: mask_intersection_ratio, parameters: {features: [a, b]}}\n    assertion: {operator: less_than_or_equal, value: 0, origin: project_config}\n", encoding="utf-8")
summary["overlap_strict_project"] = slim_result(cli(d, "review", "review", "--manifest", p, "--rules-file", rules))

# Colons are legal in feature IDs, but the overlap target is reconstructed by splitting at a colon.
d = OUT / "colon-feature-id"
features = [{"id": x, "role": "foreground", "geometry": {"type": "mask", "path": f"m{i}.png"}}
            for i, x in enumerate(("part:left", "right"))]
p = manifest(d, Image.new("RGBA", (4, 4), (120, 120, 120, 255)), features,
             overlap_constraints=[{"feature_a": "part:left", "feature_b": "right", "max_overlap_ratio": 1}])
for i in range(2):
    Image.new("L", (4, 4), 255).save(d / f"m{i}.png")
summary["colon_id"] = slim_result(cli(d, "review", "review", "--manifest", p))

# A policy requesting an anchor with no anchor evidence must remain incomplete.
d = OUT / "missing-anchor"
d.mkdir(exist_ok=True)
trace = {"schema_version": "1", "timebase": "seconds", "tracks": [{"id": "root", "kind": "position",
    "units": "m", "coordinate_system": "world", "samples": [
        {"time": 0, "value": [0, 0]}, {"time": 1, "value": [0, 0]}]}]}
save_json(d / "trace.json", trace)
save_json(d / "policy.json", {"max_position_step": 0, "max_anchor_distance": 0})
summary["missing_anchor"] = slim_result(cli(d, "game", "game", "analyze", "--trace", d / "trace.json", "--policy", d / "policy.json"))

# A declared mask hash must match the bytes on disk.
d = OUT / "mask-hash"
p = manifest(d, Image.new("RGBA", (4, 4), (100, 100, 100, 255)), [{"id": "body", "role": "foreground",
    "geometry": {"type": "mask", "path": "mask.png", "sha256": "0" * 64}}])
Image.new("L", (4, 4), 255).save(d / "mask.png")
summary["mask_hash_mismatch"] = slim_result(cli(d, "validate", "manifest", "validate", p))

# Valid, distinct finite times whose floating-point midpoints collapse.
d = OUT / "timestamp-midpoint"
d.mkdir(exist_ok=True)
trace = {"schema_version": "1", "timebase": "seconds", "tracks": [{"id": "root", "kind": "position",
    "units": "m", "coordinate_system": "world", "samples": [
        {"time": 10**16 + offset, "value": [0, 0]} for offset in (2, 4, 6)]}]}
save_json(d / "trace.json", trace)
save_json(d / "policy.json", {"max_position_step": 0})
summary["timestamp_midpoint"] = slim_result(cli(d, "game", "game", "analyze", "--trace", d / "trace.json", "--policy", d / "policy.json"))

# Each step is finite, but their arithmetic sum exceeds the floating point range.
d = OUT / "mean-step-overflow"
d.mkdir(exist_ok=True)
trace["tracks"][0]["samples"] = [{"time": i, "value": [value, 0]} for i, value in enumerate((0, 1e308, 0))]
save_json(d / "trace.json", trace)
save_json(d / "policy.json", {"max_position_step": 1e308})
result = cli(d, "game", "game", "analyze", "--trace", d / "trace.json", "--policy", d / "policy.json")
data = result["data"] or {}
summary["mean_step_overflow"] = {"exit": result["exit"], "strict_json": result["strict_json"],
    "status": data.get("status"), "completeness": data.get("completeness"),
    "stdout_contains_Infinity": "Infinity" in (d / "game.stdout.txt").read_text(encoding="utf-8")}

# Current code fingerprints make the inspected uncommitted snapshot identifiable.
summary["snapshot"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
    ROOT / "imreview/core.py", ROOT / "imreview/game.py", ROOT / "imreview/policies.py",
    ROOT / "imreview/measurements.py", ROOT / "imreview/rules/algorithms.yaml")}
save_json(OUT / "evidence.json", summary)
print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
