"""Explicit Godot/Unity runtime capture orchestration."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .render_evidence import MAX_FRAME_PIXELS, load_render_evidence
from .render_evidence import analyze_render_evidence
from .game import aggregate_game_gate


def install_unity_capture_adapter(project_root: Path) -> dict[str, str]:
    """Copy the Unity runtime/editor capture hooks without launching the project."""
    root = project_root.expanduser().resolve(strict=True)
    if not (root / "ProjectSettings" / "ProjectVersion.txt").is_file() or not (root / "Assets").is_dir():
        raise ValueError(f"Unity project markers not found at project root: {root}")
    source_root = Path(__file__).resolve().parent / "engine_resources" / "unity"
    mappings = {
        source_root / "Runtime" / "i_mage_not_aiRenderCapture.cs": root / "Assets" / "i_mage_not_ai" / "Runtime" / "i_mage_not_aiRenderCapture.cs",
        source_root / "Editor" / "i_mage_not_aiRenderCaptureEditor.cs": root / "Assets" / "Editor" / "i_mage_not_aiRenderCaptureEditor.cs",
    }
    missing = [source for source in mappings if not source.is_file()]
    if missing:
        raise ValueError(f"bundled Unity capture adapter is missing: {missing[0]}")
    existing = [destination for destination in mappings.values() if destination.exists()]
    if existing:
        raise ValueError(f"refusing to overwrite existing Unity capture adapter file: {existing[0]}")
    for destination in mappings.values():
        destination.parent.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for source, destination in mappings.items():
            shutil.copy2(source, destination)
            copied.append(destination)
    except OSError:
        for destination in copied:
            destination.unlink(missing_ok=True)
        raise
    return {str(source): str(destination) for source, destination in mappings.items()}


def run_engine_capture(*, engine: str, project_root: Path, engine_executable: Path,
                       selection: str, scenario_id: str, output: Path,
                       width: int, height: int, frame_count: int, fps: int,
                       allow_project_code: bool, event_map: Path | None = None,
                       camera_node_path: str | None = None, camera_distance_offset: float = 0.0,
                       camera_zoom_scale: float = 1.0,
                       timeout_seconds: int = 300) -> dict[str, Any]:
    """Run a project-selected engine scene and validate its rendered evidence bundle."""
    if not allow_project_code:
        raise ValueError("engine capture can execute project/editor code; pass --allow-project-code to proceed")
    if engine not in {"godot", "unity"}:
        raise ValueError("engine must be godot or unity")
    if event_map is not None and engine != "godot":
        raise ValueError("--event-map is for Godot signal hooks; Unity projects must call i_mage_not_aiRenderCapture.MarkEvent from runtime events")
    if not scenario_id.strip():
        raise ValueError("scenario_id must be non-empty")
    if (not isinstance(camera_distance_offset, (int, float)) or isinstance(camera_distance_offset, bool) or
        not isinstance(camera_zoom_scale, (int, float)) or isinstance(camera_zoom_scale, bool) or
        not math.isfinite(camera_distance_offset) or not math.isfinite(camera_zoom_scale) or camera_zoom_scale <= 0):
        raise ValueError("camera_distance_offset must be finite and camera_zoom_scale must be finite and positive")
    if camera_distance_offset and not camera_node_path:
        raise ValueError("camera_distance_offset requires camera_node_path")
    if camera_zoom_scale != 1.0 and not camera_node_path:
        raise ValueError("camera_zoom_scale requires camera_node_path")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (width, height, frame_count, fps)):
        raise ValueError("resolution, frame_count, and fps must be integers")
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > MAX_FRAME_PIXELS:
        raise ValueError(f"capture resolution must be positive and no larger than {MAX_FRAME_PIXELS} pixels")
    if not 2 <= frame_count <= 10000:
        raise ValueError("frame_count must be between 2 and 10000")
    if not 1 <= fps <= 240:
        raise ValueError("fps must be between 1 and 240")
    if not 1 <= timeout_seconds <= 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds")
    root = project_root.expanduser().resolve(strict=True)
    executable = engine_executable.expanduser().resolve(strict=True)
    output_path = output.expanduser().resolve()
    if not root.is_dir() or not executable.is_file():
        raise ValueError("project root and engine executable must exist")
    if output_path.exists() or not output_path.parent.is_dir():
        raise ValueError("output must be a new evidence JSON path in an existing directory")
    frame_dir = output_path.with_suffix("").with_name(output_path.stem + "_frames")
    if frame_dir.exists():
        raise ValueError(f"capture frame directory already exists: {frame_dir}")
    event_map_path: Path | None = None
    if event_map is not None:
        event_map_path = event_map.expanduser().resolve(strict=True)
        try:
            event_data = json.loads(event_map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read event map: {exc}") from exc
        if not isinstance(event_data, dict) or set(event_data) != {"events"} or not isinstance(event_data["events"], list):
            raise ValueError("event map must be a JSON object containing only an events array")

    if engine == "godot":
        if not (root / "project.godot").is_file():
            raise ValueError(f"Godot project.godot not found at project root: {root}")
        scene = (root / selection).resolve(strict=True)
        if not scene.is_file() or not scene.is_relative_to(root) or scene.suffix.lower() != ".tscn":
            raise ValueError("Godot selection must be an existing .tscn scene inside the project root")
        runner = Path(__file__).resolve().parent / "engine_resources" / "godot" / "i_mage_not_ai_render_capture.gd"
        command = [str(executable), "--path", str(root), "--script", str(runner), "--",
                   "--scene", "res://" + scene.relative_to(root).as_posix(), "--scenario-id", scenario_id,
                   "--output", str(output_path), "--frames", str(frame_count), "--fps", str(fps),
                   "--width", str(width), "--height", str(height)]
        if event_map_path is not None:
            command.extend(["--event-map", str(event_map_path)])
        if camera_node_path:
            command.extend(["--camera-node-path", camera_node_path,
                            "--camera-distance-offset", str(camera_distance_offset),
                            "--camera-zoom-scale", str(camera_zoom_scale)])
    else:
        if not (root / "ProjectSettings" / "ProjectVersion.txt").is_file() or not (root / "Assets").is_dir():
            raise ValueError(f"Unity project markers not found at project root: {root}")
        scene = (root / selection).resolve(strict=True)
        if not scene.is_file() or not scene.is_relative_to(root / "Assets") or scene.suffix.lower() != ".unity":
            raise ValueError("Unity selection must be an existing .unity scene inside Assets/")
        editor_adapter = root / "Assets" / "Editor" / "i_mage_not_aiRenderCaptureEditor.cs"
        runtime_adapter = root / "Assets" / "i_mage_not_ai" / "Runtime" / "i_mage_not_aiRenderCapture.cs"
        if not editor_adapter.is_file() or not runtime_adapter.is_file():
            raise ValueError("Unity capture adapter is not installed; run project install-capture-adapter first")
        unity_cli=executable.with_suffix(".com") if sys.platform=="win32" and executable.suffix.lower()==".exe" and executable.with_suffix(".com").is_file() else executable
        command = [str(unity_cli), "-batchmode", "-projectPath", str(root),
                   "-executeMethod", "i_mage_not_aiRenderCaptureEditor.RunFromCommandLine",
                   "-i_mage_not_aiScene", scene.relative_to(root).as_posix(),
                   "-i_mage_not_aiScenario", scenario_id, "-i_mage_not_aiOutput", str(output_path),
                   "-i_mage_not_aiFrames", str(frame_count), "-i_mage_not_aiFps", str(fps),
                   "-i_mage_not_aiWidth", str(width), "-i_mage_not_aiHeight", str(height),
                   "-logFile", str(output_path.with_suffix(".engine.log"))]
        if camera_node_path:
            command.extend(["-i_mage_not_aiCameraNodePath", camera_node_path,
                            "-i_mage_not_aiCameraDistanceOffset", str(camera_distance_offset),
                            "-i_mage_not_aiCameraZoomScale", str(camera_zoom_scale)])
    try:
        process = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=timeout_seconds, check=False, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{engine} capture timed out after {timeout_seconds}s; project code may still have side effects") from exc
    if process.returncode != 0:
        log_path=output_path.with_suffix(".engine.log")
        engine_log=log_path.read_text(encoding="utf-8",errors="replace")[-4000:] if log_path.is_file() else ""
        raise RuntimeError(f"{engine} capture exited {process.returncode}; stdout={process.stdout[-1000:]!r}; stderr={process.stderr[-1000:]!r}; engine_log={engine_log!r}")
    if not output_path.is_file():
        log_path=output_path.with_suffix(".engine.log")
        engine_log=log_path.read_text(encoding="utf-8",errors="replace")[-4000:] if log_path.is_file() else ""
        raise RuntimeError(f"{engine} exited {process.returncode} but did not create capture evidence: {output_path}; engine_log={engine_log!r}")
    try:
        evidence, _ = load_render_evidence(output_path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"engine capture output is not valid rendered evidence: {exc}") from exc
    expected = {"engine": engine, "scenario_id": scenario_id, "resolution": [width, height]}
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise RuntimeError(f"capture evidence {key} mismatch: expected {value!r}, got {evidence.get(key)!r}")
    if len(evidence["captures"]) != frame_count:
        raise RuntimeError(f"capture count mismatch: expected {frame_count}, got {len(evidence['captures'])}")
    return {"status": "CAPTURED", "engine": engine, "engine_version": evidence["engine_version"],
            "platform": evidence["platform"], "scenario_id": scenario_id, "resolution": [width, height],
            "capture_count": frame_count, "evidence": str(output_path), "frames_directory": str(frame_dir),
            "code_execution_acknowledged": True,
            "note": "The selected scene, its scripts, and engine/editor code ran during capture."}


def run_engine_capture_suite(*, engine: str, project_root: Path, engine_executable: Path,
                             selection: str, scenario_id: str, suite_file: Path,
                             event_map: Path | None, render_policy_file: Path,
                             review_report_file: Path, runtime_report_file: Path,
                             output: Path, frame_count: int, fps: int,
                             allow_project_code: bool, timeout_seconds: int = 300) -> dict[str, Any]:
    """Capture every declared resolution/camera case and run render-check plus game gate."""
    suite_path = suite_file.expanduser().resolve(strict=True)
    policy_path = render_policy_file.expanduser().resolve(strict=True)
    review_path = review_report_file.expanduser().resolve(strict=True)
    runtime_path = runtime_report_file.expanduser().resolve(strict=True)
    output_path = output.expanduser().resolve()
    case_directory = output_path.with_suffix("")
    if output_path.exists() or case_directory.exists() or not output_path.parent.is_dir():
        raise ValueError("suite output and companion case directory must be new paths in an existing directory")
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        render_policy = json.loads(policy_path.read_text(encoding="utf-8"))
        review_report = json.loads(review_path.read_text(encoding="utf-8"))
        runtime_report = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read capture-suite input: {exc}") from exc
    if not isinstance(suite, dict) or set(suite) != {"cases"} or not isinstance(suite["cases"], list) or not suite["cases"]:
        raise ValueError("capture suite must be an object containing a non-empty cases array")
    if len(suite["cases"]) > 100:
        raise ValueError("capture suite is limited to 100 cases")
    if not isinstance(render_policy, dict) or not render_policy:
        raise ValueError("render policy must be a non-empty JSON object")
    if not isinstance(review_report, dict) or not isinstance(runtime_report, dict):
        raise ValueError("review and runtime reports must be JSON objects")

    validated_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, case in enumerate(suite["cases"]):
        allowed = {"id", "width", "height", "camera_node_path", "camera_distance_offset", "camera_zoom_scale", "scene"}
        if not isinstance(case, dict) or set(case) - allowed or not {"id", "width", "height"} <= set(case):
            raise ValueError(f"cases[{index}] requires id, width, and height; only camera_node_path, camera_distance_offset, camera_zoom_scale, and scene are optional")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in case_id):
            raise ValueError(f"cases[{index}].id must be a unique non-empty filename-safe identifier")
        seen_ids.add(case_id)
        width, height = case["width"], case["height"]
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (width, height)) or not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > MAX_FRAME_PIXELS:
            raise ValueError(f"cases[{index}] resolution is invalid")
        camera_path = case.get("camera_node_path")
        if camera_path is not None and (not isinstance(camera_path, str) or not camera_path.strip()):
            raise ValueError(f"cases[{index}].camera_node_path must be a non-empty string")
        offset = case.get("camera_distance_offset", 0.0)
        if not isinstance(offset, (int, float)) or isinstance(offset, bool) or not math.isfinite(offset):
            raise ValueError(f"cases[{index}].camera_distance_offset must be finite")
        if offset and not camera_path:
            raise ValueError(f"cases[{index}].camera_distance_offset requires camera_node_path")
        zoom_scale = case.get("camera_zoom_scale", 1.0)
        if not isinstance(zoom_scale, (int, float)) or isinstance(zoom_scale, bool) or not math.isfinite(zoom_scale) or zoom_scale <= 0:
            raise ValueError(f"cases[{index}].camera_zoom_scale must be finite and positive")
        if zoom_scale != 1.0 and not camera_path:
            raise ValueError(f"cases[{index}].camera_zoom_scale requires camera_node_path")
        scene = case.get("scene", selection)
        if not isinstance(scene, str) or not scene.strip():
            raise ValueError(f"cases[{index}].scene must be a non-empty project-relative scene path")
        validated_cases.append({**case, "scene": scene, "camera_node_path": camera_path,
                                "camera_distance_offset": float(offset), "camera_zoom_scale": float(zoom_scale)})

    case_directory.mkdir(parents=False)
    results: list[dict[str, Any]] = []
    for case in validated_cases:
        case_id = case["id"]
        evidence_path = case_directory / f"{case_id}.evidence.json"
        render_report_path = case_directory / f"{case_id}.render-check.json"
        gate_report_path = case_directory / f"{case_id}.game-gate.json"
        try:
            capture = run_engine_capture(engine=engine, project_root=project_root,
                engine_executable=engine_executable, selection=case["scene"], scenario_id=scenario_id,
                output=evidence_path, width=case["width"], height=case["height"], frame_count=frame_count,
                fps=fps, allow_project_code=allow_project_code, event_map=event_map,
                camera_node_path=case["camera_node_path"], camera_distance_offset=case["camera_distance_offset"],
                camera_zoom_scale=case["camera_zoom_scale"],
                timeout_seconds=timeout_seconds)
            evidence, _ = load_render_evidence(evidence_path)
            render_report = analyze_render_evidence(evidence, render_policy)
            render_report_path.write_text(json.dumps(render_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            gate_report = aggregate_game_gate(review_report, runtime_report, render_report)
            gate_report_path.write_text(json.dumps(gate_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            results.append({"case_id": case_id, "status": gate_report["status"],
                "completeness": gate_report["completeness"], "resolution": [case["width"], case["height"]],
                "camera_node_path": case["camera_node_path"], "camera_distance_offset": case["camera_distance_offset"],
                "camera_zoom_scale": case["camera_zoom_scale"],
                "capture": capture, "render_report": str(render_report_path), "game_gate_report": str(gate_report_path)})
        except (OSError, ValueError, RuntimeError) as exc:
            results.append({"case_id": case_id, "status": "INCONCLUSIVE", "completeness": "INCOMPLETE",
                "resolution": [case["width"], case["height"]], "camera_node_path": case["camera_node_path"],
                "camera_distance_offset": case["camera_distance_offset"], "camera_zoom_scale": case["camera_zoom_scale"],
                "error": str(exc)})
    statuses = [item["status"] for item in results]
    status = "FAIL" if "FAIL" in statuses else ("PASS" if all(item["status"] == "PASS" and item["completeness"] == "COMPLETE" for item in results) else "INCONCLUSIVE")
    report = {"schema_version": "0.5", "asset_type": "game_capture_suite", "status": status,
        "completeness": "COMPLETE" if all(item["completeness"] == "COMPLETE" for item in results) else "INCOMPLETE",
        "engine": engine, "scenario_id": scenario_id, "case_count": len(results), "results": results}
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
