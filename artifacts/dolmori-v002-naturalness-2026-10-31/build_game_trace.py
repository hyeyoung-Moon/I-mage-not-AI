#!/usr/bin/env python3
"""Convert the Unity v002 runtime receipt and authored frames to i-mage-not-ai trace evidence."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"


def read(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def percentile(values, q):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def frame_features(path: Path):
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3].astype(np.float64) / 255.0
    mask = alpha > 0
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return {"bbox": None, "area": 0, "centroid": None, "alpha": alpha,
                "mask16": np.zeros((16, 16), dtype=np.float64), "support": None}
    weights = alpha[mask]
    cx = float(np.average(xs, weights=weights)); cy = float(np.average(ys, weights=weights))
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    mask16 = np.asarray(Image.fromarray(alpha.astype(np.float32), mode="F").resize((16, 16), Image.Resampling.BOX), dtype=np.float64)
    bottom = int(ys.max())
    support_x = xs[ys == bottom]
    return {"bbox": bbox, "area": int(mask.sum()), "centroid": [cx, cy], "alpha": alpha,
            "mask16": mask16, "support": {"y": bottom, "x_min": int(support_x.min()), "x_max": int(support_x.max()), "pixel_count": int(len(support_x))}}


def silhouette_iou(a, b):
    aa = a["alpha"] > 0; bb = b["alpha"] > 0
    union = int(np.logical_or(aa, bb).sum())
    return float(np.logical_and(aa, bb).sum() / union) if union else None


def max_vector_delta(values):
    deltas = [math.dist(a, b) for a, b in zip(values, values[1:])]
    return max(deltas, default=0.0), deltas


def build_board_layer_frames(contract, sample_rows):
    groups = {g["runtime_key"]: g for g in contract["groups"]}
    by_group = {}
    for row in sample_rows:
        if "/Walk/Anim/" not in row["animation"]:
            continue
        by_group.setdefault(float(row["time"]), {})[int(row["direction"])] = row
    moments = [(t, dirs) for t, dirs in sorted(by_group.items()) if len(dirs) == 8]
    if not moments:
        return []
    stride = max(1, math.ceil(len(moments) / 32))
    selected = moments[::stride]
    if selected[-1][0] != moments[-1][0]:
        selected.append(moments[-1])
    layer_dir = ROOT / "board_layers"
    layer_dir.mkdir(exist_ok=True)
    canvas_w, canvas_h = 300, 180
    world_x0, world_y_top = 190.0, 430.0
    game_frames = []
    for frame_number, (_time, directions) in enumerate(selected):
        layers = []
        for direction in range(8):
            row = directions[direction]
            group = groups.get(row["animation"])
            if group is None:
                continue
            frame_index = int(row["frame"])
            timeline = group.get("timeline", [])
            if frame_index >= len(timeline):
                continue
            key_id = int(timeline[frame_index]["keyframe"])
            metadata = next((k for k in group.get("keyframes", []) if int(k["keyframe"]) == key_id), None)
            if metadata is None:
                continue
            src = FRAMES / Path(metadata["path"]).name
            with Image.open(src) as im:
                rgba = im.convert("RGBA")
            v = metadata["frame_v"]
            source_w, source_h = int(v[1]), int(v[2])
            crop_w, crop_h = int(v[5]), int(v[6])
            scale_x = float(row["sprite_width"]) / crop_w
            scale_y = float(row["sprite_height"]) / crop_h
            resized = rgba.resize((max(1, round(source_w * scale_x)), max(1, round(source_h * scale_y))), Image.Resampling.BILINEAR)
            left = round(float(row["x"]) - world_x0 - source_w * scale_x / 2)
            top = round(world_y_top - float(row["y"]) - source_h * scale_y / 2)
            layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            layer.alpha_composite(resized, (left, top))
            name = f"f{frame_number:03d}-unit{direction}.png"
            relative = Path("board_layers") / name
            layer.save(ROOT / relative, format="PNG", optimize=True)
            layers.append({"id": f"unit-{direction}", "path": relative.as_posix(),
                           "z_index": int(row.get("z_order", direction))})
        game_frames.append({"time": frame_number / 60.0, "frame_index": frame_number, "layers": layers})
    return game_frames


def build():
    contract = read("unity-source-contract.json")
    samples_doc = read("unity-runtime-samples.json")
    receipt = read("unity-receipt.json")
    runtime_metadata = read("unity-runtime-metadata.json")
    tracks = []
    positions = {}
    for direction in range(8):
        rows = [s for s in samples_doc["samples"]
                if s["direction"] == direction and s["animation"] == f"Normal/Walk/Anim/{direction}"]
        by_time = {}
        for row in rows:
            by_time[float(row["time"])] = row
        rows = [by_time[t] for t in sorted(by_time)]
        pos = [[float(s["x"]), float(s["y"])] for s in rows]
        times = [float(s["time"]) for s in rows]
        positions[str(direction)] = {"times": times, "values": pos,
                                     "sprite_bottom_y": [float(s["sprite_bottom_y"]) for s in rows]}
        tracks.append({"id": f"walk-direction-{direction}", "kind": "position", "units": "ui_pixel",
                       "coordinate_system": "unity_canvas_world", "interpolation": "runtime_sampled",
                       "samples": [{"time": t, "frame_index": i, "value": p}
                                   for i, (t, p) in enumerate(zip(times, pos))]})

    static_groups = []
    clip_durations = []
    for group in contract["groups"]:
        keyframes = {int(k["keyframe"]): k for k in group.get("keyframes", [])}
        sequence = []
        timestamps = []
        now = 0.0
        for index, segment in enumerate(group.get("timeline", [])):
            key = int(segment["keyframe"])
            meta = keyframes.get(key)
            if meta is None:
                continue
            source = FRAMES / Path(meta["path"]).name
            feat = frame_features(source)
            sequence.append((key, feat, meta, source))
            timestamps.append(now)
            ticks = float(segment.get("ticks", 0))
            duration = ticks / float(group.get("tick_rate", 36))
            clip_durations.append({"group": group["group"], "frame_index": index,
                                   "keyframe": key, "start_time_seconds": now,
                                   "duration_seconds": duration, "ticks": ticks})
            now += duration
        if not sequence:
            continue
        unique_alpha = []
        for key, feat, meta, _ in sequence:
            unique_alpha.append(feat["mask16"].reshape(-1).tolist())
        if group.get("loop") and len(sequence) >= 2:
            tracks.append({"id": f"pose-loop-{group['group']}", "kind": "pose",
                           "units": "16x16_alpha_occupancy_fraction", "coordinate_system": "sprite_canvas",
                           "interpolation": "step",
                           "samples": [{"time": t, "frame_index": i, "value": v}
                                       for i, (t, v) in enumerate(zip(timestamps, unique_alpha))]})
        frame_iou = [silhouette_iou(a[1], b[1]) for a, b in zip(sequence, sequence[1:])]
        centroid_deltas = [math.dist(a[1]["centroid"], b[1]["centroid"])
                           for a, b in zip(sequence, sequence[1:])
                           if a[1]["centroid"] is not None and b[1]["centroid"] is not None]
        support_y = [x[1]["support"]["y"] for x in sequence if x[1]["support"] is not None]
        loop_iou = silhouette_iou(sequence[-1][1], sequence[0][1]) if group.get("loop") else None
        loop_pose_delta = float(np.linalg.norm(sequence[-1][1]["mask16"] - sequence[0][1]["mask16"])) if group.get("loop") else None
        static_groups.append({
            "group": group["group"], "runtime_key": group["runtime_key"], "loop": bool(group.get("loop")),
            "tick_rate": group.get("tick_rate"), "total_ticks": group.get("total_ticks"),
            "logical_frame_count": len(sequence), "unique_keyframe_count": len({x[0] for x in sequence}),
            "frame_to_frame": {"minimum_silhouette_iou": min(frame_iou) if frame_iou else None,
                               "mean_silhouette_iou": mean(frame_iou) if frame_iou else None,
                               "maximum_alpha_centroid_step_source_px": max(centroid_deltas, default=None)},
            "loop_seam": {"first_last_silhouette_iou": loop_iou,
                          "first_last_alpha_grid_l2": loop_pose_delta} if group.get("loop") else None,
            "support_line_y_range_source_px": (max(support_y) - min(support_y)) if support_y else None,
            "frames": [{"logical_index": i, "keyframe": key, "path": str(source.relative_to(ROOT)).replace("\\", "/"),
                        "bbox_xyxy": feat["bbox"], "opaque_pixel_count": feat["area"],
                        "alpha_centroid_xy": feat["centroid"], "support": feat["support"]}
                       for i, (key, feat, _meta, source) in enumerate(sequence)]
        })

    # Runtime frame dwell times account for PAC's Attack speed scaling, without a pass/fail threshold.
    transition_rows = samples_doc.get("transitions", [])
    timing = []
    groups_by_key = {g["runtime_key"]: g for g in contract["groups"]}
    grouped = {}
    for row in transition_rows:
        grouped.setdefault((int(row["direction"]), row["animation"]), []).append(row)
    for (direction, key), rows in sorted(grouped.items()):
        group = groups_by_key.get(key)
        if not group:
            continue
        timeline = group.get("timeline", [])
        rows.sort(key=lambda x: float(x["time"]))
        expected_ratios = []; absolute_errors = []
        left_censored_intervals = 0
        action = key.split("/")[1] if len(key.split("/")) > 1 else ""
        speed_scale = (float(runtime_metadata.get("delay", 0)) * (.4 + 100 * .007) / 36
                       if action == "Rotate" else 1.0)
        speed_scale = max(.01, speed_scale)
        for interval_index, (current, nxt) in enumerate(zip(rows, rows[1:])):
            frame = int(current["frame"]); next_frame = int(nxt["frame"])
            if next_frame != (frame + 1) % len(timeline) or frame >= len(timeline):
                continue
            authored = float(timeline[frame].get("ticks", 0)) / float(group.get("tick_rate", 36))
            expected = authored / speed_scale
            observed = float(nxt["time"]) - float(current["time"])
            if expected > 0 and observed > 0:
                # The first observed frame can begin partway through its dwell because
                # collection starts after Unity has already entered the clip.
                if interval_index == 0:
                    left_censored_intervals += 1
                    continue
                absolute_errors.append(abs(observed - expected))
                expected_ratios.append(observed / expected)
        timing.append({"direction": direction, "runtime_key": key, "observed_transitions": len(rows),
                       "action_speed_scale": speed_scale,
                       "left_censored_intervals_excluded": left_censored_intervals,
                       "matched_frame_dwell_samples": len(absolute_errors),
                       "median_observed_to_authored_duration_ratio": median(expected_ratios) if expected_ratios else None,
                       "p95_absolute_dwell_error_seconds": percentile(absolute_errors, 95),
                       "max_absolute_dwell_error_seconds": max(absolute_errors, default=None)})

    board_frames = build_board_layer_frames(contract, samples_doc["samples"])
    trace = {"schema_version": "1", "engine": "unity", "engine_version": receipt["unity"],
             "timebase": "seconds", "tracks": tracks, "events": [], "particles": [], "contacts": [],
             "anchors": [], "frames": board_frames}
    policy = {"max_position_step": None, "max_velocity": None, "max_acceleration": None, "max_jerk": None,
              "max_pose_loop_boundary_distance": None, "max_alpha_occlusion_ratio": None}
    (ROOT / "unity-game-trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
    (ROOT / "measurement-policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")

    # Independent measurements supplement the CLI findings with authored silhouette and timeline evidence.
    runtime_motion = {}
    for direction, data in positions.items():
        ts, xy = data["times"], data["values"]
        steps = [math.dist(a, b) for a, b in zip(xy, xy[1:])]
        velocities = [step / (tb - ta) for step, ta, tb in zip(steps, ts, ts[1:]) if tb > ta]
        mids = [(a + b) / 2 for a, b in zip(ts, ts[1:]) if b > a]
        vvec = [[(b[axis] - a[axis]) / (tb - ta) for axis in range(2)]
                for a, b, ta, tb in zip(xy, xy[1:], ts, ts[1:]) if tb > ta]
        avec = [[(b[axis] - a[axis]) / (tb - ta) for axis in range(2)]
                for a, b, ta, tb in zip(vvec, vvec[1:], mids, mids[1:]) if tb > ta]
        amids = [(a + b) / 2 for a, b in zip(mids, mids[1:]) if b > a]
        jvec = [[(b[axis] - a[axis]) / (tb - ta) for axis in range(2)]
                for a, b, ta, tb in zip(avec, avec[1:], amids, amids[1:]) if tb > ta]
        accel = [math.hypot(*v) for v in avec]; jerk = [math.hypot(*v) for v in jvec]
        support = data["sprite_bottom_y"]
        runtime_motion[direction] = {
            "position_samples": len(xy), "path_length_px": float(sum(steps)),
            "max_step_px": max(steps, default=0.0), "p95_step_px": percentile(steps, 95),
            "max_speed_px_per_second": max(velocities, default=0.0), "p95_speed_px_per_second": percentile(velocities, 95),
            "max_acceleration_px_per_second2": max(accel, default=0.0), "p95_acceleration_px_per_second2": percentile(accel, 95),
            "max_jerk_px_per_second3": max(jerk, default=0.0), "p95_jerk_px_per_second3": percentile(jerk, 95),
            "sprite_bottom_anchor_range_px": max(support) - min(support) if support else None,
            "position_start_xy": xy[0] if xy else None, "position_end_xy": xy[-1] if xy else None,
        }

    events = read("frame-event-map.json")
    manifest = read("source-manifest.json")
    pac_effect_route = read("pac-effects-eruption-route.json")
    clip_rows = runtime_metadata.get("clips", [])
    shadow_clip_count = sum("/Shadow/" in str(c.get("key", "")) for c in clip_rows)
    clips_by_action = {}
    for clip in clip_rows:
        action = str(clip.get("key", "")).split("/")[1] if "/" in str(clip.get("key", "")) else ""
        if action:
            clips_by_action[action] = clips_by_action.get(action, 0) + 1
    effect_plan = events.get("eruptions") or {}
    animation_inventory = {
        "runtime_clip_count": len(clip_rows),
        "runtime_clip_counts_by_action": clips_by_action,
        "gameplay_action_to_animation": runtime_metadata.get("actions", []),
        "frame_timing_measurement_groups": len(timing),
        "semantic_events": {
            "attack_contact_logical_frame": events.get("attackContactLogicalFrame"),
            "ability_hit_logical_frame": events.get("abilityHitLogicalFrame"),
            "runtime_event_verified": events.get("runtimeEventVerified", False),
        },
        "particle_effect_plan": {
            "combat_source": effect_plan.get("combatSource"),
            "planned_projectiles": effect_plan.get("plannedProjectiles"),
            "planned_schedule_ms": effect_plan.get("scheduleMs", []),
            "body_keyframe_binding": effect_plan.get("bodyKeyframeBinding"),
            "physical_emitter": effect_plan.get("physicalEmitter"),
            "runtime_verified": effect_plan.get("runtimeVerified", False),
        },
        "pac_effect_catalog_candidate": {
            "route_key": pac_effect_route["route"]["key"],
            "route_step_kind": pac_effect_route["route"]["steps"][0]["kind"],
            "animation_key": pac_effect_route["animation"]["key"],
            "animation_frame_count": len(pac_effect_route["animation"]["frames"]),
            "animation_fps": pac_effect_route["animation"]["fps"],
            "route_duration_ms": pac_effect_route["route"]["steps"][0]["duration"],
            "start_coords": pac_effect_route["route"]["steps"][0]["startCoords"],
            "start_offset": pac_effect_route["route"]["steps"][0]["startOffset"],
            "runtime_exercised_in_run20": pac_effect_route["runtime_exercised_in_run20"],
            "catalog_sha256": pac_effect_route["catalog_sha256"],
        },
    }
    coverage = {
        "scope": "2D sprite animation; 3D rotation and skeletal bone metrics excluded by design",
        "runtime": {"unity_version": receipt["unity"], "graphics_api": receipt["graphicsApi"],
                    "headless": receipt["graphicsApi"] == "Null", "runtime_checks_passed": receipt["passed"],
                    "check_count": len(receipt["checks"]), "runtime_sample_count": len(samples_doc["samples"]),
                    "transition_count": len(transition_rows)},
        "source_contract": {"version": manifest.get("version"), "unity_status": manifest.get("unityStatus"),
                            "runtime_event_verified": manifest.get("runtimeEventVerified"),
                            "timed_groups": sum(not g.get("untimed", False) for g in contract["groups"]),
                            "total_groups": len(contract["groups"]), "events_runtime_verified": events.get("runtimeEventVerified"),
                            "attack_contact_logical_frame": events.get("attackContactLogicalFrame"),
                            "ability_hit_logical_frame": events.get("abilityHitLogicalFrame"),
                            "physical_emitter": (events.get("eruptions") or {}).get("physicalEmitter")},
        "measured": {"movement_tracks": 8, "runtime_frame_timing_groups": len(timing),
                     "source_silhouette_groups": len(static_groups), "runtime_board_occlusion_frames": len(board_frames),
                     "source_loop_groups": sum(bool(g["loop"]) for g in static_groups),
                     "available_action_animation_playback_and_frame_timing": "measured for clips present in the Unity runtime metadata",
                     "inter_character_board_occlusion": "measured from runtime positions, source alpha, and Unity sibling order",
                     "internal_part_or_shadow_occlusion": f"not measured: source has {shadow_clip_count} shadow clips and no independently authored character-part layers",
                     "foot_contact_sliding": "not measured: no per-foot contact labels or contact intervals are authored",
                     "attack_contact_and_ability_hit_timestamps": "not measured: semantic event frame markers are null and runtime event verification is false",
                     "particle_lifetime_bounds_and_runtime_emission": "not validated for Dolmori's eruption plan: PAC has a generic ERUPTION sprite-projectile route, but the plan-to-route mapping, body-keyframe binding, 30-shot runtime evidence, lifetimes, and bounds are absent; run20 did not dispatch an ABILITY event",
                     "expected_callbacks": "not measured: this isolated visual QA has callbacksExpected=false",
                     "screen_pixel_rendering": "not validated: Unity runtime used Null graphics device"},
        "animation_inventory": animation_inventory,
        "movement_by_direction": runtime_motion,
        "authored_frame_geometry_and_loop_seams": static_groups,
        "runtime_frame_dwell_vs_authored_ticks": timing,
        "runtime_action_speed_scale": {"attack_formula": "max(0.01, character.delay * (0.4 + speed * 0.007) / 36)",
                                        "fixture_speed": 100, "character_delay": runtime_metadata.get("delay"),
                                        "attack_scale": max(.01, float(runtime_metadata.get("delay", 0)) * (.4 + 100 * .007) / 36)},
        "measurement_policy": "numeric values are measurements only; no aesthetic pass thresholds were supplied",
        "source_sha256": {"atlas": receipt["atlasSha256"], "runtime_metadata": receipt["metadataSha256"],
                          "contract": hashlib.sha256((ROOT / "source-motion-contract.json").read_bytes()).hexdigest()},
    }
    (ROOT / "naturalness-coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")


if __name__ == "__main__":
    build()
