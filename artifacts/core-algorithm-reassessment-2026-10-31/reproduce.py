"""Read-only source audit; writes its own evidence and uses temporary fixtures."""
from __future__ import annotations

import hashlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import warnings

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from imreview.core import _delta_e_2000, _delta_e_2000_array, load_manifest, review
from imreview.benchmark import create_submission_template, generate_synthetic_dataset, verify_synthetic_submission
from imreview.game import GameTrace, analyze_trace
from imreview.measurements import compare_feature_context, measure_manifest
from imreview.policies import load_project_rules, match_project_rules


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli(*args):
    run = subprocess.run([sys.executable, "-m", "i_mage_not_ai", *map(str, args)],
                         cwd=ROOT, capture_output=True, text=True)
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError:
        payload = run.stdout
    return {"exit_code": run.returncode, "report": payload, "stderr": run.stderr}


def manifest(root, asset="asset.png", **kwargs):
    value = {"schema_version": "0.5", "asset": {"path": asset, "sha256": digest(root / asset)}, "features": [], **kwargs}
    path = root / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def rule(identifier, algorithm, value, *, match=None, priority=0, operator="less_than_or_equal", parameters=None, **kwargs):
    return {"id": identifier, "version": "1.0", "priority": priority, "match": match or {},
            "algorithm": {"id": algorithm, "version": "1.0", "parameters": parameters or {}},
            "assertion": {"operator": operator, "value": value, "origin": "project_config"}, **kwargs}


def rules_file(root, rules):
    path = root / "rules.yaml"
    path.write_text(json.dumps({"schema_version": "1", "rules": rules}), encoding="utf-8")
    return path


def fixture(root, size=(8, 8)):
    Image.new("RGBA", size, (255, 0, 0, 255)).save(root / "asset.png")
    Image.new("L", size, 255).save(root / "mask.png")
    return {"id": "shape", "role": "body", "geometry": {"type": "mask", "path": "mask.png"}}


def main():
    out = Path(__file__).resolve().parent
    evidence = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "python": sys.version, "sources": {str(p.relative_to(ROOT)): digest(p) for p in (ROOT / "imreview").glob("*.py")}, "cases": {}}
    cases = evidence["cases"]
    with tempfile.TemporaryDirectory(prefix="imreview-reassessment-") as tmp:
        root = Path(tmp)
        feature = fixture(root)
        image = Image.open(root / "asset.png").convert("RGBA")
        image.putpixel((0, 0), (0, 255, 0, 255)); image.save(root / "asset.png")
        path = manifest(root, intent={"asset_type": "icon", "style": "flat"}, features=[feature])
        policies = rules_file(root, [
            rule("ICON-STRICT", "quantized_palette_count", 1, match={"asset_type": "icon", "feature_role": "body"}, priority=10, parameters={"bin_size": [1, 1, 1]}),
            rule("SPRITE-LOOSE", "quantized_palette_count", 5, match={"asset_type": "sprite", "feature_role": "body"}, priority=20, parameters={"bin_size": [1, 1, 1]}),
        ])
        doc, source = load_manifest(path)
        matched = match_project_rules(doc, load_project_rules(policies))
        cases["asset_selector_bypass"] = {"expected": "ICON-STRICT applies; 2 colors > 1 => FAIL. SPRITE-LOOSE must not match icon.",
            "matched_rule_ids": [r.id for r, _ in matched], "cli": cli("review", "--manifest", path, "--rules-file", policies)}

        feature = fixture(root)
        a = Image.new("L", (8, 8), 0); a.putpixel((0, 0), 255); a.save(root / "a.png")
        b = Image.new("L", (8, 8), 0); b.putpixel((7, 7), 255); b.save(root / "b.png")
        path = manifest(root, features=[{"id": key, "role": "body", "geometry": {"type": "mask", "path": f"{key}.png"}} for key in ("a", "b")])
        policies = rules_file(root, [rule("MIN-OVERLAP", "mask_intersection_ratio", .5, operator="greater_than_or_equal", parameters={"features": ["a", "b"]})])
        cases["overlap_operator_reversed"] = {"expected": "Reject unsupported >= or evaluate 0 >= 0.5 as FAIL.",
            "cli": cli("review", "--manifest", path, "--rules-file", policies)}

        trace = {"schema_version": "1", "timebase": "seconds", "tracks": [{"id": "p", "kind": "position", "units": "m", "coordinate_system": "world",
                  "samples": [{"time": 0, "value": [0, 0]}, {"time": 1, "value": [0, 0]}]}]}
        (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        policy = {"max_position_step": 0, "max_anchor_distance": 0}
        (root / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        path = manifest(root, "trace.json", intent={"kind": "game_trace"}, game_policy=policy)
        cases["standalone_game_missing_anchor"] = {"expected": "Missing requested anchor evidence => INCONCLUSIVE/INCOMPLETE in both entry points.",
            "game_cli": cli("game", "analyze", "--trace", root / "trace.json", "--policy", root / "policy.json"),
            "manifest_cli": cli("review", "--manifest", path)}

        pose_trace = {**trace, "tracks": [trace["tracks"][0], {"id": "pose", "kind": "pose", "units": "m", "coordinate_system": "world",
            "samples": [{"time": 0, "value": {"hip": [0, 0]}}, {"time": 1, "value": {"knee": [0, 0]}}]}]}
        cases["pose_keys_not_compared"] = {"expected": "Different joint identity cannot prove a matching loop boundary.",
            "report": analyze_trace(GameTrace.model_validate(pose_trace), {"max_position_step": 0, "max_pose_loop_boundary_distance": 0})}

        extreme = {**trace, "tracks": [{**trace["tracks"][0], "samples": [{"time": 0, "value": [0, 0]}, {"time": 1, "value": [1e200, 0]}]}]}
        try:
            report = analyze_trace(GameTrace.model_validate(extreme), {"max_position_step": 1})
        except Exception as exc:
            report = {"exception": type(exc).__name__, "message": str(exc)}
        cases["finite_position_norm_overflow"] = {"expected": "Distance 1e200 is finite and exceeds 1 => FAIL; avoid arithmetic exception.", "observed": report}

        feature = fixture(root)
        path = manifest(root, features=[{**feature, "properties": {"luma_bin_width": .5}}])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            measured = measure_manifest(path)
        cases["fractional_luma_bin_truncated"] = {"expected": "Accepted positive bin width 0.5 remains 0.5; zero cannot be silently substituted.",
            "luma_levels": measured["features"][0]["metrics"]["luma_levels"], "warnings": [str(w.message) for w in caught]}

        image = Image.new("RGBA", (2, 1), (128, 128, 128, 255)); image.putpixel((1, 0), (160, 160, 160, 255)); image.save(root / "asset.png")
        Image.new("L", (2, 1), 255).save(root / "mask.png")
        path = manifest(root, features=[{**feature, "properties": {"max_luma_levels": 1, "luma_bin_width": 32}}])
        doc, source = load_manifest(path)
        cases["luminance_definitions_differ"] = {"expected": "Interpretation differs: review uses encoded RGB weights; measure uses linearized sRGB. Labels/versions should make this explicit.",
            "review": next(r for r in review(doc, source)["results"] if r["algorithm"] == "masked_luma_levels"),
            "measure": measure_manifest(path)["features"][0]["metrics"]["luma_levels"]}

        image = Image.new("RGBA", (8, 8), (0, 0, 0, 255))
        for y in range(8):
            for x in range(4): image.putpixel((x, y), (255, 255, 255, 255))
        image.save(root / "asset.png")
        m = Image.new("L", (8, 8), 0)
        for y in range(8):
            for x in range(4): m.putpixel((x, y), 255)
        m.save(root / "mask.png")
        path = manifest(root, features=[feature])
        cases["constant_black_white_standardized_gap"] = {"expected": "Different constant means with zero pooled standard deviation => undefined (or infinity), not zero.",
            "observed": compare_feature_context(path, "shape")["luminance_relationship"]}

        # Recheck fixes from the previous audit against validated manifests.
        feature = fixture(root)
        path = manifest(root, features=[feature])
        policies = rules_file(root, [rule("REQUIRES-DEPTH", "exact_palette_count", 1, requires=["depth_buffer"])])
        cases["prior_missing_capability_fix"] = cli("review", "--manifest", path, "--rules-file", policies)
        policies = rules_file(root, [rule("VERSION", "exact_palette_count", 1)])
        raw = json.loads(policies.read_text()); raw["rules"][0]["algorithm"]["version"] = "99.0"; policies.write_text(json.dumps(raw))
        cases["prior_unsupported_version_fix"] = cli("review", "--manifest", path, "--rules-file", policies)
        cases["prior_empty_position_fix"] = analyze_trace(GameTrace.model_validate({**trace, "tracks": [{**trace["tracks"][0], "samples": []}]}), {"max_position_step": 0})

        dataset_root = root / "dataset"
        dataset = generate_synthetic_dataset(dataset_root, 2)
        submission_path = root / "submission.json"
        submission = create_submission_template(dataset_root / "dataset.json", submission_path)
        meta = {"agent": "audit", "model": "audit", "prompt_version": "1", "environment": "local"}
        submission["protocol"].update(prompt_sha256="a" * 64, environment_image="audit", seed=0)
        expected_outside_changes = 0
        protected_pixels = 0
        for case in dataset["cases"]:
            with Image.open(dataset_root / case["clean_asset"]) as opened:
                result = np.asarray(opened.convert("RGBA")).copy()
            allowed = np.zeros(result.shape[:2], dtype=bool)
            for x1, y1, x2, y2 in case["allowed_regions"]:
                allowed[y1:y2, x1:x2] = True
            protected_pixels += int((~allowed).sum())
            if case["injected_rule"] == "GEOMETRY-002":
                result[~allowed, 0] ^= 1
                expected_outside_changes += int((~allowed).sum())
            dest = root / (case["case_id"] + "-output.png")
            Image.fromarray(result, "RGBA").save(dest)
            for name in ("agent_only", "agent_plus_tool"):
                submission["tracks"][name]["assets"][case["case_id"]] = dest.name
        for track in submission["tracks"].values():
            track["metadata"] = dict(meta)
        submission_path.write_text(json.dumps(submission), encoding="utf-8")
        scored = verify_synthetic_submission(dataset_root / "dataset.json", submission_path)
        cases["preservation_denominator_ignores_allowed_area"] = {
            "protected_pixel_count": protected_pixels, "outside_changed_pixel_count": expected_outside_changes,
            "expected_protected_pixel_preservation": 1 - expected_outside_changes / protected_pixels,
            "reported": scored["tracks"]["agent_plus_tool"]}

        spec = importlib.util.spec_from_file_location("audit_v2_scorer", ROOT / "benchmarks/score_multimetric_ab_v2.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RUN = root / "cache-probe"
        simple = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        for y in range(2, 6):
            for x in range(2, 6): simple.putpixel((x, y), (255, 0, 0, 255))
        simple.save(root / "asset.png")
        mask_path = module.RUN / "sessions/audit/outputs/output-recheck/probe/silhouette.png"
        initial = module.cli_report("audit", "probe", root / "asset.png", mask_path, {"max_centroid": 1, "max_palette": 2, "components": 1})
        cached = module.cli_report("audit", "probe", root / "asset.png", mask_path, {"max_centroid": 1, "max_palette": 0, "components": 1})
        cases["v2_cache_does_not_key_policy"] = {"expected": "Changing palette limit from 2 to 0 must recompute; 1 visible color exceeds 0.",
            "initial_status": initial["status"], "new_requested_palette_limit": 0,
            "cached_status": cached["status"], "cached_palette_finding": next(r for r in cached["results"] if r["rule_id"] == "COLOR-001")}

    url = "https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt"
    try:
        content = urllib.request.urlopen(url, timeout=30).read()
        data = np.loadtxt(io.BytesIO(content))
        scalar = np.array([_delta_e_2000(row[:3], row[3:6]) for row in data])
        vector = _delta_e_2000_array(data[:, :3], data[:, 3:6])
        evidence["ciede2000_reference"] = {"url": url, "sha256": hashlib.sha256(content).hexdigest(), "pairs": len(data),
            "maximum_absolute_error_scalar": float(np.max(abs(scalar-data[:, 6]))),
            "maximum_absolute_error_vector": float(np.max(abs(vector-data[:, 6]))),
            "pass_at_reference_rounding_tolerance_0_00005": bool(np.all(abs(vector-data[:, 6]) <= .00005))}
    except Exception as exc:
        evidence["ciede2000_reference"] = {"error": str(exc)}
    evidence["source_hashes_unchanged"] = all(digest(ROOT / p) == value for p, value in evidence["sources"].items())
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"evidence_path": str(out / "evidence.json"), "cases": list(cases), "ciede2000_reference": evidence["ciede2000_reference"],
                      "source_hashes_unchanged": evidence["source_hashes_unchanged"]}, indent=2))


if __name__ == "__main__":
    main()
