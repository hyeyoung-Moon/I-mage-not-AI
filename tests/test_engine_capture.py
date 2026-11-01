from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

import i_mage_not_ai.engine_capture as engine_capture
from i_mage_not_ai.engine_capture import install_unity_capture_adapter, run_engine_capture, run_engine_capture_suite


def test_engine_capture_requires_explicit_project_code_acknowledgment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--allow-project-code"):
        run_engine_capture(
            engine="godot", project_root=tmp_path / "missing-project", engine_executable=tmp_path / "missing-engine",
            selection="scene.tscn", scenario_id="scenario", output=tmp_path / "capture.json",
            width=320, height=180, frame_count=2, fps=30, allow_project_code=False,
        )


def test_camera_distance_offset_requires_a_camera_node(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires camera_node_path"):
        run_engine_capture(
            engine="godot", project_root=tmp_path / "missing-project", engine_executable=tmp_path / "missing-engine",
            selection="scene.tscn", scenario_id="scenario", output=tmp_path / "capture.json",
            width=320, height=180, frame_count=2, fps=30, allow_project_code=True,
            camera_distance_offset=1.0,
        )


def test_unity_rejects_godot_event_map(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Godot signal hooks"):
        run_engine_capture(
            engine="unity", project_root=tmp_path / "missing-project", engine_executable=tmp_path / "missing-engine",
            selection="scene.unity", scenario_id="scenario", output=tmp_path / "capture.json",
            width=320, height=180, frame_count=2, fps=30, allow_project_code=True, event_map=tmp_path / "events.json",
        )


def test_unity_capture_adapter_install_is_additive_and_repeat_install_refuses_overwrite(tmp_path: Path) -> None:
    project = tmp_path / "unity-project"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.25f1\n", encoding="utf-8")

    installed = install_unity_capture_adapter(project)
    assert len(installed) == 2
    assert all(Path(destination).is_file() for destination in installed.values())
    with pytest.raises(ValueError, match="refusing to overwrite"):
        install_unity_capture_adapter(project)


def test_capture_suite_runs_render_check_and_game_gate_for_each_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"cases": [
        {"id": "portrait", "width": 4, "height": 6},
        {"id": "landscape-far", "width": 6, "height": 4,
         "camera_node_path": "Camera", "camera_distance_offset": 2.0, "camera_zoom_scale": 1.25},
    ]}), encoding="utf-8")
    policy = tmp_path / "render-policy.json"
    policy.write_text(json.dumps({"min_capture_count": 2, "max_changed_pixel_ratio": 1.0}), encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"asset_type": "game_trace", "status": "PASS", "completeness": "COMPLETE",
                                  "scenario_id": "attack-v1"}), encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"asset_type": "runtime_evidence", "status": "PASS", "completeness": "COMPLETE",
                                   "scenario_id": "attack-v1"}), encoding="utf-8")

    def fake_capture(**kwargs: object) -> dict[str, object]:
        evidence_path = kwargs["output"]
        assert isinstance(evidence_path, Path)
        width, height = int(kwargs["width"]), int(kwargs["height"])
        frame_directory = evidence_path.parent / f"{evidence_path.stem}_frames"
        frame_directory.mkdir()
        rows = []
        for index in range(2):
            name = f"{index:06d}.png"
            Image.new("RGB", (width, height), (index * 20, 0, 0)).save(frame_directory / name)
            rows.append({"frame_index": index, "timestamp": index / 30,
                         "image": f"{evidence_path.stem}_frames/{name}", "event_ids": []})
        evidence_path.write_text(json.dumps({"format": "i_mage_not_ai.render-evidence/1", "capture_source": "engine_runtime",
            "engine": "godot", "engine_version": "4.6.3", "platform": "Windows", "scenario_id": "attack-v1",
            "timebase": "seconds", "resolution": [width, height], "import_settings": {"renderer": "vulkan"},
            "captures": rows}), encoding="utf-8")
        return {"status": "CAPTURED"}

    monkeypatch.setattr(engine_capture, "run_engine_capture", fake_capture)
    output = tmp_path / "capture-suite-report.json"
    report = run_engine_capture_suite(engine="godot", project_root=tmp_path, engine_executable=tmp_path / "godot",
        selection="scene.tscn", scenario_id="attack-v1", suite_file=suite, event_map=None,
        render_policy_file=policy, review_report_file=review, runtime_report_file=runtime,
        output=output, frame_count=2, fps=30, allow_project_code=True)

    assert (report["status"], report["completeness"], report["case_count"]) == ("PASS", "COMPLETE", 2)
    assert all(Path(row["render_report"]).is_file() and Path(row["game_gate_report"]).is_file()
               for row in report["results"])
