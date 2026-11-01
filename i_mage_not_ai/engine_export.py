"""Explicit engine CLI exporter execution, gated by a user acknowledgment."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .game import load_trace


def run_engine_export(*, engine: str, project_root: Path, engine_executable: Path,
                      selection: str, output: Path, allow_project_code: bool,
                      timeout_seconds: int = 180, overwrite: bool = False) -> dict[str, Any]:
    if not allow_project_code:
        raise ValueError("engine export can execute project/editor code; pass --allow-project-code to proceed")
    if engine not in {"godot", "unity"}:
        raise ValueError("engine must be godot or unity")
    root = project_root.expanduser().resolve(strict=True)
    executable = engine_executable.expanduser().resolve(strict=True)
    output_path = output.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    if not executable.is_file():
        raise ValueError(f"engine executable is not a file: {executable}")
    if timeout_seconds < 1 or timeout_seconds > 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds")
    if output_path.exists() and not overwrite:
        raise ValueError(f"output already exists; choose another path or pass --overwrite: {output_path}")
    if not output_path.parent.is_dir():
        raise ValueError(f"output directory does not exist: {output_path.parent}")

    if engine == "godot":
        if not (root / "project.godot").is_file():
            raise ValueError(f"Godot project.godot not found at project root: {root}")
        scene = (root / selection).resolve(strict=True)
        if not scene.is_file() or not scene.is_relative_to(root) or scene.suffix.lower() != ".tscn":
            raise ValueError("Godot selection must be an existing .tscn scene inside the project root")
        exporter = Path(__file__).resolve().parent / "engine_resources" / "godot" / "i_mage_not_ai_cli_export.gd"
        if not exporter.is_file():
            raise ValueError(f"bundled Godot CLI exporter is missing: {exporter}")
        scene_argument = "res://" + scene.relative_to(root).as_posix()
        command = [str(executable), "--headless", "--path", str(root), "--script", str(exporter), "--",
                   "--scene", scene_argument, "--output", str(output_path)]
    else:
        if not (root / "ProjectSettings" / "ProjectVersion.txt").is_file() or not (root / "Assets").is_dir():
            raise ValueError(f"Unity project markers not found at project root: {root}")
        clip = (root / selection).resolve(strict=True)
        if not clip.is_file() or not clip.is_relative_to(root / "Assets") or clip.suffix.lower() != ".anim":
            raise ValueError("Unity selection must be an existing .anim file inside Assets/")
        exporter = root / "Assets" / "Editor" / "i_mage_not_aiTraceExporter.cs"
        if not exporter.is_file():
            raise ValueError("Unity exporter is not installed. Copy the bundled Unity trace exporter into Assets/Editor/ first.")
        command = [str(executable), "-batchmode", "-nographics", "-projectPath", str(root),
                   "-executeMethod", "i_mage_not_aiTraceExporter.ExportFromCommandLine",
                   "-i_mage_not_aiClip", clip.relative_to(root).as_posix(), "-i_mage_not_aiOutput", str(output_path), "-logFile", "-", "-quit"]
    try:
        process = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                 timeout=timeout_seconds, check=False, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{engine} export timed out after {timeout_seconds}s; project code may still have side effects") from exc
    if process.returncode != 0:
        raise RuntimeError(f"{engine} exporter exited {process.returncode}; stdout={process.stdout[-4000:]!r}; stderr={process.stderr[-4000:]!r}")

    if not output_path.is_file():
        raise RuntimeError(f"engine command exited successfully but did not create the requested trace: {output_path}")
    try:
        trace = load_trace(output_path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"engine output is not a valid i_mage_not_ai trace: {exc}") from exc
    if trace.engine != engine:
        raise RuntimeError(f"exported trace engine mismatch: expected {engine}, got {trace.engine}")
    return {"status": "EXPORTED", "engine": trace.engine, "engine_version": trace.engine_version,
            "trace": str(output_path), "tracks": len(trace.tracks), "events": len(trace.events),
            "particles": len(trace.particles), "code_execution_acknowledged": True,
            "note": "The selected scene/clip and the engine editor may execute project code during loading."}
