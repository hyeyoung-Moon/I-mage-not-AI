from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from typer.testing import CliRunner

from i_mage_not_ai.core import load_manifest, review
from i_mage_not_ai.game import aggregate_game_gate
from i_mage_not_ai.typer_cli import app


def _manifest(path: Path, status: str) -> Path:
    image = path / "asset.png"
    Image.new("RGBA", (4, 4), (20, 30, 40, 255)).save(image)
    data = {
        "schema_version": "0.6",
        "asset": {"path": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest()},
        "features": [],
        "feature_inventory": [{"id": "hero-face", "description": "hero face", "status": status,
                               **({"rationale": "No face is requested for this abstract icon."} if status == "not_applicable" else {})}],
    }
    manifest = path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def test_manifest_feature_inventory_blocks_unresolved_required_feature(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, "not_found")
    manifest, root = load_manifest(manifest_path)
    report = review(manifest, root)
    gate = next(row for row in report["results"] if row["rule_id"] == "FEATURE-COVERAGE-001")
    assert (report["status"], report["completeness"]) == ("INCONCLUSIVE", "INCOMPLETE")
    assert gate["measured"]["unresolved"] == ["hero-face"]


def test_manifest_feature_inventory_allows_explicit_non_applicability(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, "not_applicable")
    manifest, root = load_manifest(manifest_path)
    report = review(manifest, root)
    gate = next(row for row in report["results"] if row["rule_id"] == "FEATURE-COVERAGE-001")
    assert gate["status"] == "PASS"
    assert report["completeness"] == "COMPLETE"


def test_candidate_rejection_is_recorded_but_unreviewed_candidate_blocks_gate(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, "approved")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["features"] = [{"id": "face", "role": "face"}]
    item = data["feature_inventory"][0]
    item.update({"feature_id": "face", "candidates": [{"candidate_id": "xal-1", "status": "approved"},
                                                          {"candidate_id": "xal-2", "status": "rejected"}]})
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    manifest, root = load_manifest(manifest_path)
    assert review(manifest, root)["completeness"] == "COMPLETE"
    item["candidates"][1]["status"] = "unreviewed"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    manifest, root = load_manifest(manifest_path)
    report = review(manifest, root)
    assert (report["status"], report["completeness"]) == ("INCONCLUSIVE", "INCOMPLETE")


def test_render_check_measures_frame_delta_and_event_time(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (4, 4), (0, 0, 0)).save(frames / "0.png")
    second = np.zeros((4, 4, 3), dtype=np.uint8)
    second[0, 0] = (255, 0, 0)
    Image.fromarray(second).save(frames / "1.png")
    evidence = {
        "format": "i_mage_not_ai.render-evidence/1", "capture_source": "engine_runtime", "engine": "godot",
        "engine_version": "4.6.3", "platform": "android", "scenario_id": "attack-v1", "timebase": "seconds",
        "resolution": [4, 4], "import_settings": {"filter": "linear", "mipmaps": True, "compression": "lossless"},
        "captures": [{"frame_index": 0, "timestamp": 0.0, "image": "frames/0.png", "event_ids": []},
                     {"frame_index": 1, "timestamp": 0.1, "image": "frames/1.png", "event_ids": ["attack"]}],
    }
    (tmp_path / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (tmp_path / "policy.json").write_text(json.dumps({"min_capture_count": 2, "max_changed_pixel_ratio": 0.1,
                                                        "pixel_delta_threshold": 10, "event_times": {"attack": {"time": 0.1, "tolerance": 0.01}}}), encoding="utf-8")
    result = CliRunner().invoke(app, ["game", "render-check", "--evidence", str(tmp_path / "evidence.json"),
                                     "--policy", str(tmp_path / "policy.json")])
    report = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert report["status"] == "PASS"
    delta = next(row for row in report["results"] if row["algorithm"] == "render_changed_pixel_ratio")
    assert delta["measured"]["max_ratio"] == 1 / 16
    timing = next(row for row in report["results"] if row["algorithm"] == "render_event_frame_alignment")
    assert timing["status"] == "PASS"


def test_render_check_requires_saved_frames_before_and_after_event(tmp_path: Path) -> None:
    from i_mage_not_ai.render_evidence import analyze_render_evidence, load_render_evidence

    frames = tmp_path / "frames"
    frames.mkdir()
    captures = []
    for index in range(4):
        name = f"{index}.png"
        Image.new("RGB", (2, 2), (index * 20, 0, 0)).save(frames / name)
        captures.append({"frame_index": index, "timestamp": index * 0.1, "image": f"frames/{name}",
                         "event_ids": ["hit"] if index == 2 else []})
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"format": "i_mage_not_ai.render-evidence/1", "capture_source": "engine_runtime",
        "engine": "godot", "engine_version": "4.6.3", "platform": "Windows", "scenario_id": "hit-v1",
        "timebase": "seconds", "resolution": [2, 2], "import_settings": {"renderer": "vulkan"}, "captures": captures}), encoding="utf-8")
    evidence, _ = load_render_evidence(evidence_path)
    policy = {"event_times": {"hit": {"time": 0.2, "tolerance": 0.01, "before_frames": 2, "after_frames": 1}}}
    report = analyze_render_evidence(evidence, policy)
    finding = next(row for row in report["results"] if row["rule_id"] == "RENDER-EVENT:hit")
    assert (report["status"], report["completeness"], finding["status"]) == ("PASS", "COMPLETE", "PASS")
    assert finding["measured"]["frame_index"] == 2
    assert finding["measured"]["available_before_frames"] == 2
    assert finding["measured"]["available_after_frames"] == 1

    policy["event_times"]["hit"]["after_frames"] = 2
    incomplete = analyze_render_evidence(evidence, policy)
    finding = next(row for row in incomplete["results"] if row["rule_id"] == "RENDER-EVENT:hit")
    assert (incomplete["status"], incomplete["completeness"], finding["status"]) == ("INCONCLUSIVE", "INCOMPLETE", "INCONCLUSIVE")


def test_render_check_fails_when_changed_pixel_ratio_exceeds_policy(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (2, 2), (0, 0, 0)).save(frames / "0.png")
    Image.new("RGB", (2, 2), (255, 255, 255)).save(frames / "1.png")
    evidence = {"format": "i_mage_not_ai.render-evidence/1", "capture_source": "engine_runtime", "engine": "unity",
                "engine_version": "6000", "platform": "desktop", "scenario_id": "idle", "timebase": "seconds",
                "resolution": [2, 2], "import_settings": {"filter": "point"},
                "captures": [{"frame_index": 0, "timestamp": 0, "image": "frames/0.png", "event_ids": []},
                             {"frame_index": 1, "timestamp": 1, "image": "frames/1.png", "event_ids": []}]}
    (tmp_path / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    policy = {"max_changed_pixel_ratio": 0.2}
    (tmp_path / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    result = CliRunner().invoke(app, ["game", "render-check", "--evidence", str(tmp_path / "evidence.json"),
                                     "--policy", str(tmp_path / "policy.json")])
    report = json.loads(result.stdout)
    assert result.exit_code == 1
    assert (report["status"], report["completeness"]) == ("FAIL", "COMPLETE")


def test_game_gate_requires_complete_reports_for_same_scenario() -> None:
    reports = [
        {"asset_type": "game_trace", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "hero-attack-v1"},
        {"asset_type": "runtime_evidence", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "hero-attack-v1"},
        {"asset_type": "render_evidence", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "hero-attack-v1"},
    ]
    report = aggregate_game_gate(*reports)
    assert (report["status"], report["completeness"]) == ("PASS", "COMPLETE")
    reports[1]["scenario_id"] = "different-run"
    mismatch = aggregate_game_gate(*reports)
    assert (mismatch["status"], mismatch["completeness"]) == ("INCONCLUSIVE", "INCOMPLETE")
    assert next(row for row in mismatch["results"] if row["gate"] == "scenario_alignment")["status"] == "INCONCLUSIVE"


def test_game_gate_cli_writes_an_inconclusive_aggregate_on_scenario_mismatch(tmp_path: Path) -> None:
    reports = [
        {"asset_type": "game_trace", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "attack-a"},
        {"asset_type": "runtime_evidence", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "attack-b"},
        {"asset_type": "render_evidence", "status": "PASS", "completeness": "COMPLETE", "scenario_id": "attack-a"},
    ]
    paths = []
    for index, report in enumerate(reports):
        path = tmp_path / f"report-{index}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "gate.json"
    result = CliRunner().invoke(app, ["game", "gate", "--review-report", str(paths[0]), "--runtime-report", str(paths[1]),
                                     "--render-report", str(paths[2]), "--output", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result.exit_code == 2
    assert (report["status"], report["completeness"]) == ("INCONCLUSIVE", "INCOMPLETE")


def test_game_gate_does_not_accept_static_image_review() -> None:
    report = aggregate_game_gate(
        {"status": "PASS", "completeness": "COMPLETE", "asset_type": "image"},
        {"status": "PASS", "completeness": "COMPLETE", "asset_type": "runtime_evidence", "scenario_id": "s"},
        {"status": "PASS", "completeness": "COMPLETE", "asset_type": "render_evidence", "scenario_id": "s"},
    )
    assert (report["status"], report["completeness"]) == ("INCONCLUSIVE", "INCOMPLETE")


def test_render_flash_gate_measures_count_area_and_duration(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    colors = [(0, 0, 0), (255, 0, 0), (0, 0, 0), (0, 0, 0)]
    captures = []
    for index, color in enumerate(colors):
        name = f"{index}.png"
        Image.new("RGB", (2, 2), color).save(frames / name)
        captures.append({"frame_index": index, "timestamp": index * 0.1, "image": f"frames/{name}",
                         "event_ids": []})
    evidence = {"format": "i_mage_not_ai.render-evidence/1", "capture_source": "engine_runtime", "engine": "godot",
                "engine_version": "4.6.3", "platform": "android", "scenario_id": "flash-v1", "timebase": "seconds",
                "resolution": [2, 2], "import_settings": {"filter": "linear"}, "captures": captures}
    (tmp_path / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    policy = {"flash": {"min_luminance_delta": 40, "min_area_ratio": 0.5, "max_events": 1,
                         "max_rate_hz": 5, "max_area_ratio": 1, "max_red_area_ratio": 1,
                         "red_min_dominance": 1.2, "max_event_duration_seconds": 0.2}}
    (tmp_path / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    result = CliRunner().invoke(app, ["game", "render-check", "--evidence", str(tmp_path / "evidence.json"),
                                     "--policy", str(tmp_path / "policy.json")])
    report = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    flash = next(row for row in report["results"] if row["algorithm"] == "render_flash_metrics")
    assert flash["measured"]["event_count"] == 1
    assert flash["measured"]["event_durations_seconds"] == [0.2]
