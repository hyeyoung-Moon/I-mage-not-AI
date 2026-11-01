"""Deterministic checks for engine-rendered frame evidence bundles."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


FORMAT = "i_mage_not_ai.render-evidence/1"
MAX_FRAME_PIXELS = 16_777_216


def load_render_evidence(path: Path) -> tuple[dict[str, Any], Path]:
    """Load and validate a bundle of actual engine capture frames."""
    try:
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("render evidence exceeds 50 MiB")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read render evidence: {exc}") from exc
    required = {"format", "capture_source", "engine", "engine_version", "platform", "scenario_id", "timebase", "resolution", "import_settings", "captures"}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError(f"render evidence must contain exactly: {sorted(required)}")
    if data["format"] != FORMAT or data["capture_source"] != "engine_runtime":
        raise ValueError(f"render evidence must use {FORMAT} and capture_source=engine_runtime")
    for key in ("engine_version", "platform", "scenario_id"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if data["engine"] not in {"godot", "unity"} or data["timebase"] not in {"seconds", "monotonic_elapsed_seconds"}:
        raise ValueError("render evidence requires engine godot/unity and a supported seconds timebase")
    resolution = data["resolution"]
    if not isinstance(resolution, list) or len(resolution) != 2 or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in resolution):
        raise ValueError("resolution must be [positive_width, positive_height]")
    if resolution[0] * resolution[1] > 50_000_000:
        raise ValueError("render resolution exceeds 50 million pixels")
    if not isinstance(data["import_settings"], dict) or not data["import_settings"]:
        raise ValueError("import_settings must record the effective engine import/render settings")
    captures = data["captures"]
    if not isinstance(captures, list):
        raise ValueError("captures must be an array")
    root = path.resolve().parent
    previous_time = -math.inf
    previous_index = -1
    seen_events: dict[str, list[float]] = {}
    event_positions: dict[str, list[dict[str, int | float]]] = {}
    capture_rows: list[dict[str, Any]] = []
    for capture_position, item in enumerate(captures):
        if not isinstance(item, dict) or set(item) != {"frame_index", "timestamp", "image", "event_ids"}:
            raise ValueError("each capture requires frame_index, timestamp, image, and event_ids")
        index, timestamp, image_path, event_ids = item["frame_index"], item["timestamp"], item["image"], item["event_ids"]
        if not isinstance(index, int) or isinstance(index, bool) or index <= previous_index:
            raise ValueError("capture frame_index values must be non-negative and strictly increasing")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or not math.isfinite(timestamp) or timestamp < 0 or timestamp <= previous_time:
            raise ValueError("capture timestamps must be finite, non-negative, and strictly increasing")
        if not isinstance(image_path, str) or not image_path:
            raise ValueError("capture image must be a relative path")
        if not isinstance(event_ids, list) or any(not isinstance(event_id, str) or not event_id.strip() for event_id in event_ids) or len(event_ids) != len(set(event_ids)):
            raise ValueError("event_ids must contain unique non-empty strings")
        relative = Path(image_path)
        resolved = (root / relative).resolve()
        if relative.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError(f"capture image is missing or escapes the evidence directory: {image_path}")
        if resolved.stat().st_size > 50 * 1024 * 1024:
            raise ValueError(f"capture image exceeds 50 MiB: {image_path}")
        try:
            with Image.open(resolved) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1 or image.size != tuple(resolution):
                    raise ValueError(f"capture must be a single-frame PNG at declared resolution: {image_path}")
                if image.width * image.height > MAX_FRAME_PIXELS:
                    raise ValueError(f"capture exceeds the decoded frame limit of {MAX_FRAME_PIXELS} pixels: {image_path}")
        except OSError as exc:
            raise ValueError(f"cannot decode capture image {image_path}: {exc}") from exc
        for event_id in event_ids:
            seen_events.setdefault(event_id, []).append(float(timestamp))
            event_positions.setdefault(event_id, []).append({
                "capture_position": capture_position,
                "frame_index": index,
                "timestamp": float(timestamp),
            })
        capture_rows.append({"frame_index": index, "timestamp": float(timestamp), "image": image_path,
                             "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(), "event_ids": event_ids})
        previous_time, previous_index = float(timestamp), index
    data["_capture_rows"] = capture_rows
    data["_evidence_root"] = root
    data["_events"] = seen_events
    data["_event_positions"] = event_positions
    return data, root


def _load_frame(root: Path, row: dict[str, Any]) -> np.ndarray:
    with Image.open(root / str(row["image"])) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def analyze_render_evidence(evidence: dict[str, Any], policy: Any) -> dict[str, Any]:
    """Measure consecutive rendered pixel changes and event-to-frame timing."""
    if not isinstance(policy, dict) or not policy:
        raise ValueError("render policy must define at least one explicit check")
    allowed = {"min_capture_count", "max_changed_pixel_ratio", "pixel_delta_threshold", "event_times", "flash"}
    if set(policy) - allowed:
        raise ValueError(f"unknown render policy keys: {sorted(set(policy)-allowed)}")
    findings: list[dict[str, Any]] = []
    root: Path = evidence["_evidence_root"]
    rows: list[dict[str, Any]] = evidence["_capture_rows"]
    count = len(rows)
    if "min_capture_count" in policy:
        minimum = policy["min_capture_count"]
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 2:
            raise ValueError("min_capture_count must be an integer >= 2")
        state = "PASS" if count >= minimum else "FAIL"
        findings.append({"rule_id": "RENDER-CAPTURE-COUNT", "algorithm": "render_capture_count", "status": state,
                         "measured": {"count": count}, "threshold": {"minimum": minimum},
                         "formula": "captured engine frames >= min_capture_count"})
    if "max_changed_pixel_ratio" in policy:
        maximum = policy["max_changed_pixel_ratio"]
        threshold = policy.get("pixel_delta_threshold", 1)
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool) or not math.isfinite(maximum) or not 0 <= maximum <= 1:
            raise ValueError("max_changed_pixel_ratio must be between 0 and 1")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or not 1 <= threshold <= 255:
            raise ValueError("pixel_delta_threshold must be an integer from 1 to 255")
        if count < 2:
            status, measured = "INCONCLUSIVE", None
        else:
            ratios = []
            for left_row, right_row in zip(rows, rows[1:]):
                left = _load_frame(root, left_row)
                right = _load_frame(root, right_row)
                ratios.append(float(np.mean(np.max(np.abs(right.astype(np.int16) - left.astype(np.int16)), axis=2) >= threshold)))
            maximum_observed = max(ratios)
            measured = {"max_ratio": maximum_observed, "per_interval": ratios,
                        "changed_pixel_threshold_rgb": threshold, "interval_count": len(ratios)}
            status = "PASS" if maximum_observed <= maximum else "FAIL"
        findings.append({"rule_id": "RENDER-FRAME-DELTA", "algorithm": "render_changed_pixel_ratio", "status": status,
                         "measured": measured, "threshold": {"max_ratio": maximum},
                         "formula": "count(pixels with max_channel_abs_delta >= threshold) / total_pixels per adjacent capture"})
    event_times = policy.get("event_times", {})
    if not isinstance(event_times, dict):
        raise ValueError("event_times must map event IDs to {time, tolerance}")
    observed: dict[str, list[float]] = evidence["_events"]
    event_positions: dict[str, list[dict[str, int | float]]] = evidence["_event_positions"]
    for event_id, expected in sorted(event_times.items()):
        if not isinstance(expected, dict) or not {"time", "tolerance"} <= set(expected) or set(expected) - {"time", "tolerance", "before_frames", "after_frames"}:
            raise ValueError(f"event_times[{event_id}] requires time and tolerance, with optional before_frames and after_frames")
        time, tolerance = expected["time"], expected["tolerance"]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in (time, tolerance)):
            raise ValueError(f"event_times[{event_id}] time/tolerance must be finite and non-negative")
        times = observed.get(event_id, [])
        before_frames = expected.get("before_frames", 0)
        after_frames = expected.get("after_frames", 0)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (before_frames, after_frames)):
            raise ValueError(f"event_times[{event_id}] before_frames and after_frames must be non-negative integers")
        if len(times) != 1:
            status, measured = "INCONCLUSIVE", {"observed_timestamps": times}
        else:
            error = abs(times[0] - float(time))
            event_row = event_positions[event_id][0]
            position = int(event_row["capture_position"])
            available_before = position
            available_after = count - position - 1
            before_start = max(0, position - before_frames)
            after_end = min(count - 1, position + after_frames)
            window_indices = list(range(before_start, after_end + 1))
            contiguous = all(
                int(rows[right]["frame_index"]) == int(rows[left]["frame_index"]) + 1
                for left, right in zip(window_indices, window_indices[1:])
            )
            window_complete = (available_before >= before_frames and available_after >= after_frames and contiguous)
            measured = {"observed_timestamp": times[0], "frame_index": event_row["frame_index"],
                        "absolute_error": error, "timebase": evidence["timebase"],
                        "requested_before_frames": before_frames, "available_before_frames": available_before,
                        "requested_after_frames": after_frames, "available_after_frames": available_after,
                        "frame_indices_contiguous": contiguous}
            status = "FAIL" if error > tolerance else ("PASS" if window_complete else "INCONCLUSIVE")
        findings.append({"rule_id": f"RENDER-EVENT:{event_id}", "algorithm": "render_event_frame_alignment", "status": status,
                         "measured": measured, "threshold": {"expected_time": time, "tolerance": tolerance,
                             "before_frames": before_frames, "after_frames": after_frames},
                         "formula": "exactly one event annotation, timestamp within tolerance, and requested neighboring frames exist"})
    flash_policy = policy.get("flash")
    if flash_policy is not None:
        if not isinstance(flash_policy, dict):
            raise ValueError("flash must be an object")
        required = {"min_luminance_delta", "min_area_ratio"}
        supported = {"max_events", "max_rate_hz", "max_area_ratio", "max_red_area_ratio", "max_event_duration_seconds", "red_min_dominance"}
        if not required <= set(flash_policy) or set(flash_policy) - required - supported:
            raise ValueError("flash requires min_luminance_delta and min_area_ratio, plus supported optional limits")
        delta_threshold, area_threshold = flash_policy["min_luminance_delta"], flash_policy["min_area_ratio"]
        if not isinstance(delta_threshold, (int, float)) or isinstance(delta_threshold, bool) or not math.isfinite(delta_threshold) or not 0 < delta_threshold <= 255:
            raise ValueError("flash.min_luminance_delta must be in (0, 255]")
        if not isinstance(area_threshold, (int, float)) or isinstance(area_threshold, bool) or not math.isfinite(area_threshold) or not 0 < area_threshold <= 1:
            raise ValueError("flash.min_area_ratio must be in (0, 1]")
        for key, value in flash_policy.items():
            if key in required or key == "red_min_dominance":
                continue
            lower = 0 if key in {"max_events", "max_rate_hz", "max_event_duration_seconds"} else 0
            upper = 1 if key in {"max_area_ratio", "max_red_area_ratio"} else None
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < lower or (upper is not None and value > upper):
                raise ValueError(f"flash.{key} has an invalid numeric limit")
        if "max_events" in flash_policy and (not isinstance(flash_policy["max_events"], int) or isinstance(flash_policy["max_events"], bool)):
            raise ValueError("flash.max_events must be a non-negative integer")
        if "max_red_area_ratio" in flash_policy and "red_min_dominance" not in flash_policy:
            raise ValueError("flash.red_min_dominance is required with max_red_area_ratio")
        if "max_red_area_ratio" in flash_policy:
            dominance = flash_policy["red_min_dominance"]
            if not isinstance(dominance, (int, float)) or isinstance(dominance, bool) or not math.isfinite(dominance) or dominance <= 1:
                raise ValueError("flash.red_min_dominance must be finite and greater than 1")
        if count < 2:
            measured = None
            flash_state = "INCONCLUSIVE"
        else:
            times = [row["timestamp"] for row in rows]
            interval_areas: list[float] = []
            active: list[bool] = []
            red_areas: list[float] = []
            for left_row, right_row in zip(rows, rows[1:]):
                before = _load_frame(root, left_row).astype(np.float32)
                after = _load_frame(root, right_row).astype(np.float32)
                before_luma = 0.2126 * before[:, :, 0] + 0.7152 * before[:, :, 1] + 0.0722 * before[:, :, 2]
                after_luma = 0.2126 * after[:, :, 0] + 0.7152 * after[:, :, 1] + 0.0722 * after[:, :, 2]
                changed = np.abs(after_luma - before_luma) >= float(delta_threshold)
                area = float(np.mean(changed))
                interval_areas.append(area)
                active.append(area >= area_threshold)
                if "max_red_area_ratio" in flash_policy:
                    dominance = float(flash_policy["red_min_dominance"])
                    before_red = before[:, :, 0] >= dominance * np.maximum(before[:, :, 1], before[:, :, 2])
                    after_red = after[:, :, 0] >= dominance * np.maximum(after[:, :, 1], after[:, :, 2])
                    red_areas.append(float(np.mean(changed & (before_red | after_red))))
            groups: list[tuple[int, int]] = []
            start: int | None = None
            for index, is_active in enumerate(active + [False]):
                if is_active and start is None:
                    start = index
                elif not is_active and start is not None:
                    groups.append((start, index - 1))
                    start = None
            durations = [times[end + 1] - times[start] for start, end in groups]
            red_area = max(red_areas, default=0.0)
            elapsed = times[-1] - times[0]
            measured = {"event_count": len(groups), "event_rate_hz": len(groups) / elapsed if elapsed else None,
                        "max_area_ratio": max(interval_areas, default=0.0), "red_flash_area_ratio": red_area,
                        "event_durations_seconds": durations, "cumulative_active_interval_seconds": sum(durations),
                        "interval_area_ratios": interval_areas, "luminance_delta_threshold": delta_threshold,
                        "minimum_area_for_event": area_threshold}
            violations = []
            for key, measured_key in (("max_events", "event_count"), ("max_rate_hz", "event_rate_hz"),
                                      ("max_area_ratio", "max_area_ratio"), ("max_red_area_ratio", "red_flash_area_ratio")):
                if key in flash_policy and (measured[measured_key] is None or measured[measured_key] > flash_policy[key]):
                    violations.append(key)
            if "max_event_duration_seconds" in flash_policy and any(value > flash_policy["max_event_duration_seconds"] for value in durations):
                violations.append("max_event_duration_seconds")
            flash_state = "FAIL" if violations else "PASS"
        findings.append({"rule_id": "RENDER-FLASH-001", "algorithm": "render_flash_metrics", "status": flash_state,
                         "measured": measured, "threshold": flash_policy,
                         "formula": "frame-pair luminance-delta mask area, contiguous event intervals, duration, and configured red-dominance area"})
    complete = bool(findings) and all(row["status"] in {"PASS", "FAIL"} for row in findings)
    status = "FAIL" if any(row["status"] == "FAIL" for row in findings) else ("PASS" if complete else "INCONCLUSIVE")
    return {"schema_version": "0.5", "asset_type": "render_evidence", "status": status,
            "completeness": "COMPLETE" if complete else "INCOMPLETE",
            "engine": evidence["engine"], "engine_version": evidence["engine_version"],
            "platform": evidence["platform"], "scenario_id": evidence["scenario_id"],
            "resolution": evidence["resolution"], "import_settings": evidence["import_settings"],
            "captures": evidence["_capture_rows"],
            "coverage": {"required": len(findings), "completed": sum(row["status"] in {"PASS", "FAIL"} for row in findings),
                         "missing": [row["rule_id"] for row in findings if row["status"] not in {"PASS", "FAIL"}]},
            "results": findings}
