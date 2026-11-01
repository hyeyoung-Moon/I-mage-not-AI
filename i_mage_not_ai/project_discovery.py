"""Bounded, read-only discovery for local Godot and Unity projects."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

SUPPORTED_ASSETS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
IGNORED_DIRS = {
    ".git", ".godot", ".vs", "Library", "Temp", "Logs", "obj", "bin",
    "Build", "Builds", "UserSettings", "__pycache__", ".venv", "venv", "node_modules",
}
REFERENCE_EXTENSIONS = {".tscn", ".tres", ".unity", ".prefab", ".asset", ".mat", ".controller", ".anim"}
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ASSET_HASH_BYTES = 50 * 1024 * 1024


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk(root: Path, max_files: int, max_depth: int) -> tuple[list[Path], set[Path], dict[str, Any]]:
    files: list[Path] = []
    directories: set[Path] = {root}
    ignored: Counter[str] = Counter()
    ignored_samples: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    complete = True
    stack = [(root, 0)]

    def note_ignored(path: Path, reason: str) -> None:
        ignored[reason] += 1
        if len(ignored_samples) < 100:
            ignored_samples.append({"path": _relative(path, root), "reason": reason})

    while stack:
        directory, depth = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold(), reverse=True)
        except OSError as exc:
            complete = False
            errors.append({"path": _relative(directory, root), "error": f"{type(exc).__name__}: {exc}"})
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    note_ignored(path, "symlink_not_followed")
                elif entry.is_dir(follow_symlinks=False):
                    directories.add(path)
                    if entry.name in IGNORED_DIRS:
                        note_ignored(path, "generated_or_dependency_directory")
                    elif depth >= max_depth:
                        note_ignored(path, "max_depth_reached")
                        complete = False
                    else:
                        stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    if len(files) >= max_files:
                        note_ignored(path, "max_files_reached")
                        complete = False
                        stack.clear()
                        break
                    files.append(path)
                else:
                    note_ignored(path, "special_file_not_read")
            except OSError as exc:
                complete = False
                errors.append({"path": _relative(path, root), "error": f"{type(exc).__name__}: {exc}"})

    files.sort(key=lambda path: _relative(path, root).casefold())
    return files, directories, {
        "complete": complete,
        "ignored_counts": dict(sorted(ignored.items())),
        "ignored_samples": ignored_samples,
        "errors": errors[:100],
        "error_count": len(errors),
    }


def _find_projects(root: Path, files: list[Path], directories: set[Path]) -> list[dict[str, Any]]:
    projects: dict[Path, dict[str, Any]] = {}
    for path in files:
        if path.name.casefold() == "project.godot":
            project_root = path.parent
            projects[project_root] = {"engine": "godot", "project_root": project_root}
        elif path.parts[-2:] == ("ProjectSettings", "ProjectVersion.txt"):
            project_root = path.parent.parent
            if project_root / "Assets" in directories:
                projects[project_root] = {"engine": "unity", "project_root": project_root}

    records: list[dict[str, Any]] = []
    for project_root, info in sorted(projects.items(), key=lambda item: _relative(item[0], root).casefold()):
        engine = info["engine"]
        record: dict[str, Any] = {"engine": engine, "root": _relative(project_root, root) or ".", "metadata": {}}
        if engine == "godot":
            config_path = project_root / "project.godot"
            try:
                contents = config_path.read_text(encoding="utf-8", errors="replace")[:MAX_METADATA_BYTES]
                name_match = re.search(r'(?m)^config/name\s*=\s*"([^"]*)"', contents)
                scene_match = re.search(r'(?m)^run/main_scene\s*=\s*"res://([^"]+)"', contents)
                if name_match:
                    record["metadata"]["name"] = name_match.group(1)
                if scene_match:
                    record["metadata"]["main_scene"] = scene_match.group(1)
            except OSError as exc:
                record["metadata_error"] = f"{type(exc).__name__}: {exc}"
        else:
            version_path = project_root / "ProjectSettings" / "ProjectVersion.txt"
            try:
                contents = version_path.read_text(encoding="utf-8", errors="replace")[:MAX_METADATA_BYTES]
                version_match = re.search(r'(?m)^m_EditorVersion:\s*(\S+)', contents)
                if version_match:
                    record["metadata"]["editor_version"] = version_match.group(1)
            except OSError as exc:
                record["metadata_error"] = f"{type(exc).__name__}: {exc}"
        record["_root_path"] = project_root
        records.append(record)
    return records


def _image_info(path: Path, project_root: Path, root: Path) -> dict[str, Any]:
    relative = _relative(path, project_root)
    row: dict[str, Any] = {"path": relative, "extension": path.suffix.lower(), "size_bytes": path.stat().st_size,
                           "format": None, "width": None, "height": None, "alpha": None,
                           "referenced_by": [], "import_settings": {}}
    if row["size_bytes"] > MAX_ASSET_HASH_BYTES:
        row["inspection"] = "too_large_for_safe_header_inspection"
        return row
    if path.suffix.lower() == ".svg":
        row["format"] = "SVG"
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
            width_match = re.search(r'<svg\b[^>]*\bwidth\s*=\s*["\']([^"\']+)', content, re.I)
            height_match = re.search(r'<svg\b[^>]*\bheight\s*=\s*["\']([^"\']+)', content, re.I)
            row["width"] = width_match.group(1) if width_match else None
            row["height"] = height_match.group(1) if height_match else None
        except (OSError, UnicodeError):
            row["inspection"] = "unreadable_svg_header"
        return row
    try:
        with Image.open(path) as image:
            row.update({"format": image.format, "width": image.width, "height": image.height,
                        "alpha": "A" in image.getbands() or "transparency" in image.info})
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        row["inspection"] = f"unreadable_image: {type(exc).__name__}"
    return row


def _references(project_root: Path, files: list[Path], assets: list[dict[str, Any]]) -> None:
    by_relative = {item["path"]: item for item in assets}
    godot_assets = {f"res://{name}": row for name, row in by_relative.items()}
    unity_guid_to_asset: dict[str, dict[str, Any]] = {}
    for image_path, row in by_relative.items():
        meta_path = project_root / f"{image_path}.meta"
        if meta_path.is_file() and meta_path.stat().st_size <= MAX_METADATA_BYTES:
            text = meta_path.read_text(encoding="utf-8", errors="replace")
            guid_match = re.search(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$", text)
            if guid_match:
                unity_guid_to_asset[guid_match.group(1).lower()] = row
            sprite_mode = re.search(r"(?m)^\s+spriteMode:\s*(\d+)", text)
            if sprite_mode:
                row["import_settings"]["sprite_mode"] = int(sprite_mode.group(1))

    for path in files:
        relative = _relative(path, project_root)
        suffix = path.suffix.lower()
        if suffix not in REFERENCE_EXTENSIONS or path.stat().st_size > MAX_METADATA_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if relative.split("/", 1)[0] == "Assets":
            referenced_guids = {match.group(1).lower() for match in re.finditer(r"\bguid:\s*([0-9a-fA-F]{32})\b", text)}
            for guid in referenced_guids:
                target = unity_guid_to_asset.get(guid)
                if target is not None:
                    target["referenced_by"].append(relative)
        else:
            resource_paths = {match.group(1) for match in re.finditer(r'\bpath\s*=\s*"res://([^"]+)"', text)}
            for resource_path in resource_paths:
                target = godot_assets.get(f"res://{resource_path}")
                if target is not None:
                    target["referenced_by"].append(relative)
    for asset in assets:
        asset["referenced_by"] = sorted(set(asset["referenced_by"]))


def discover_projects(path: Path, *, max_files: int = 20_000, max_depth: int = 16) -> dict[str, Any]:
    """Inventory engine project markers and image references without executing project code."""
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project discovery path is not a directory: {root}")
    if max_files < 1 or max_depth < 0:
        raise ValueError("max_files must be positive and max_depth cannot be negative")
    files, directories, traversal = _walk(root, max_files, max_depth)
    projects = _find_projects(root, files, directories)
    images: list[dict[str, Any]] = []
    for project in projects:
        project_root: Path = project.pop("_root_path")
        project_files = [file for file in files if file.is_relative_to(project_root)]
        assets = [_image_info(file, project_root, root) for file in project_files if file.suffix.lower() in SUPPORTED_ASSETS]
        _references(project_root, project_files, assets)
        project["asset_count"] = len(assets)
        project["assets"] = assets
        images.extend({"project": project["root"], **asset} for asset in assets)
    status = "INCOMPLETE" if not traversal["complete"] else ("READY" if projects else "NO_PROJECT_FOUND")
    warnings = ["Project contents are treated as untrusted data; no scripts, plugins, importers, or editor processes were run.",
                "Image semantics and user/project quality thresholds are not inferred by discovery."]
    if len(projects) > 1:
        warnings.append("Multiple projects were found. Select one root for manifest generation or explicit engine export.")
    return {"status": status, "read_only": True, "executed_project_code": False,
            "scan_root": str(root), "limits": {"max_files": max_files, "max_depth": max_depth},
            "coverage": {"complete": traversal["complete"], "files_seen": len(files), "project_count": len(projects),
                         "ignored_counts": traversal["ignored_counts"], "ignored_samples": traversal["ignored_samples"],
                         "errors": traversal["errors"], "error_count": traversal["error_count"]},
            "projects": projects, "images": images, "warnings": warnings}


def create_project_manifest(project_root: Path, asset_relative: str) -> dict[str, Any]:
    """Create a hash-pinned, policy-free manifest draft for a selected discovered asset."""
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    relative = Path(asset_relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("asset path must stay inside the selected project root")
    asset = (root / relative).resolve(strict=True)
    if not asset.is_file() or not asset.is_relative_to(root):
        raise ValueError("selected asset must be a regular file inside the project")
    if asset.suffix.lower() not in SUPPORTED_ASSETS:
        raise ValueError(f"unsupported image asset extension: {asset.suffix}")
    if asset.stat().st_size > MAX_ASSET_HASH_BYTES:
        raise ValueError("selected asset exceeds the 50 MiB safe manifest hashing limit")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    return {"schema_version": "0.5",
            "asset": {"id": asset.stem, "path": asset.relative_to(root).as_posix(),
                      "format": asset.suffix.lower().lstrip("."), "sha256": digest},
            "intent": {"type": "unclassified", "origin": "source_native"},
            "features": [], "constraints": {},
            "discovery": {"project_root": ".", "source": "explicit_project_selection",
                          "semantic_features_inferred": False, "thresholds_inferred": False}}


def install_unity_exporter(project_root: Path) -> Path:
    """Copy the reviewed exporter into Assets/Editor; never overwrite an existing file."""
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / "Assets").is_dir() or not (root / "ProjectSettings" / "ProjectVersion.txt").is_file():
        raise ValueError("selected directory is not a Unity project root")
    source = Path(__file__).resolve().parent / "engine_resources" / "unity" / "Editor" / "i_mage_not_aiTraceExporter.cs"
    if not source.is_file():
        raise ValueError(f"bundled Unity exporter is missing: {source}")
    destination_dir = root / "Assets" / "Editor"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists():
        raise ValueError(f"refusing to overwrite an existing exporter: {destination}")
    shutil.copyfile(source, destination)
    return destination
