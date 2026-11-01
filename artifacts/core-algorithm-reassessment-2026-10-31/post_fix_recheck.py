"""Reproduce selected post-fix review and game findings with temporary inputs."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(*args: object) -> tuple[int, dict, str]:
    run = subprocess.run([sys.executable, "-m", "i_mage_not_ai", *map(str, args)],
                         cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        doc = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI returned non-JSON output: {run.stderr}") from exc
    return run.returncode, doc, run.stdout


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def finding(report: dict, key: str, value: str) -> dict:
    return next(row for row in report["results"] if row.get(key) == value)


def main() -> None:
    results = {}
    with tempfile.TemporaryDirectory(prefix="imreview-post-fix-") as directory:
        root = Path(directory)
        source = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
        source.putpixel((0, 0), (0, 255, 0, 255))
        source.save(root / "asset.png")
        mask = Image.new("L", (4, 4), 255)
        mask.putpixel((0, 0), 0)
        mask.save(root / "mask.png")
        manifest = {"schema_version": "0.5", "asset": {"path": "asset.png", "sha256": sha(root / "asset.png")},
                    "features": [{"id": "body", "role": "body", "geometry": {"type": "mask", "path": "mask.png"},
                                  "properties": {"max_palette_colors": 1}}]}
        manifest_path = root / "manifest.json"
        write(manifest_path, manifest)
        original_manifest_hash, first_mask_hash = sha(manifest_path), sha(root / "mask.png")
        rc1, first, _ = invoke("review", "--manifest", manifest_path)
        mask.putpixel((0, 0), 255)
        mask.save(root / "mask.png")
        rc2, second, _ = invoke("review", "--manifest", manifest_path)
        results["mask_integrity"] = {
            "manifest_sha256_unchanged": original_manifest_hash == sha(manifest_path),
            "source_sha256_unchanged": first["asset"]["sha256"] == second["asset"]["sha256"],
            "mask_sha256_changed": first_mask_hash != sha(root / "mask.png"),
            "first": {"exit": rc1, "status": first["status"], "finding": finding(first, "rule_id", "COLOR-002")},
            "second": {"exit": rc2, "status": second["status"], "finding": finding(second, "rule_id", "COLOR-002")},
        }

        left = Image.new("L", (4, 4), 0)
        right = Image.new("L", (4, 4), 0)
        for y in range(4):
            for x in range(2):
                left.putpixel((x, y), 255)
                right.putpixel((x + 2, y), 255)
        left.save(root / "a.png")
        right.save(root / "b.png")
        manifest["features"] = [{"id": name, "role": "body", "geometry": {"type": "mask", "path": name + ".png"}}
                                for name in ("a", "b")]
        write(manifest_path, manifest)
        overlap = {"id": "MAX-OVERLAP", "version": "1", "match": {},
                   "algorithm": {"id": "mask_intersection_ratio", "parameters": {"features": ["a", "b"]}},
                   "assertion": {"operator": "less_than_or_equal", "value": 0.5, "origin": "project_config"}}
        rules_path = root / "rules.yaml"
        write(rules_path, {"schema_version": "1", "rules": [overlap]})
        rc, report, _ = invoke("review", "--manifest", manifest_path, "--rules-file", rules_path)
        results["valid_overlap_policy"] = {"exit": rc, "status": report["status"],
                                           "completeness": report["completeness"], "coverage": report["coverage"],
                                           "finding": finding(report, "rule_id", "MAX-OVERLAP")}
        palette = {"id": "BODY-PALETTE", "version": "1", "match": {"feature_id": "a"},
                   "algorithm": {"id": "quantized_palette_count", "parameters": {"bin_size": [10, 10, 10]}},
                   "assertion": {"operator": "less_than_or_equal", "value": 2, "origin": "project_config"}}
        write(rules_path, {"schema_version": "1", "rules": [palette]})
        rc, report, _ = invoke("review", "--manifest", manifest_path, "--rules-file", rules_path)
        results["policy_origin"] = {"exit": rc, "declared_origin": "project_config", "finding": finding(report, "rule_id", "BODY-PALETTE")}

        trace = {"schema_version": "1", "timebase": "seconds", "tracks": [
            {"id": "p", "kind": "position", "units": "m", "coordinate_system": "world",
             "samples": [{"time": 0, "value": [0, 0]}, {"time": 1, "value": [0, 0]}]},
            {"id": "pose", "kind": "pose", "units": "m", "coordinate_system": "world",
             "samples": [{"time": 0, "value": {"hip": {"left": [0, 0]}}},
                         {"time": 1, "value": {"hip": {"right": [0, 0]}}}]}]}
        trace_path, policy_path = root / "trace.json", root / "policy.json"
        write(trace_path, trace)
        write(policy_path, {"max_position_step": 0, "max_pose_loop_boundary_distance": 0})
        rc, report, _ = invoke("game", "analyze", "--trace", trace_path, "--policy", policy_path)
        results["nested_pose"] = {"exit": rc, "status": report["status"], "completeness": report["completeness"],
                                  "first_pose": trace["tracks"][1]["samples"][0]["value"],
                                  "last_pose": trace["tracks"][1]["samples"][1]["value"],
                                  "finding": finding(report, "id", "POSE-LOOP:pose")}

        trace["tracks"] = [{"id": "p", "kind": "position", "units": "m", "coordinate_system": "world",
                            "samples": [{"time": 0, "value": [0, 0]}, {"time": 1, "value": [1e200, 0]}]}]
        write(trace_path, trace)
        write(policy_path, {"max_position_step": 1e201, "max_velocity": 1e201})
        rc, report, raw = invoke("game", "analyze", "--trace", trace_path, "--policy", policy_path)
        velocity = finding(report, "id", "VELOCITY:p")
        value = velocity["measured"]["max"]
        results["extreme_velocity"] = {"exit": rc, "status": report["status"], "expected_velocity": 1e200,
                                      "limit": 1e201, "finding_status": velocity["status"],
                                      "reported_velocity": str(value) if not math.isfinite(value) else value,
                                      "raw_json_contains_infinity": "Infinity" in raw}

    result = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "source_sha256": {str(path.relative_to(ROOT)): sha(path) for path in
                                (ROOT / "imreview/core.py", ROOT / "imreview/game.py", ROOT / "imreview/policies.py")},
              "cases": results}
    target = Path(__file__).with_name("post-fix-recheck.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(target), "cases": list(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
