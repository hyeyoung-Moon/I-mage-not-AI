"""Exact-value CLI conformance fixtures for the implemented math algorithms."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import pytest
import numpy as np

from PIL import Image


def cli(*args):
    return subprocess.run([sys.executable, "-m", "i_mage_not_ai", *map(str, args)],
                          capture_output=True, text=True)


def manifest(tmp_path, asset, *, constraints=None, features=None, overlaps=None):
    doc = {"schema_version": "0.5",
           "asset": {"path": asset.name, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()},
           "features": features or [], "constraints": constraints or {}}
    if overlaps is not None:
        doc["overlap_constraints"] = overlaps
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def review(tmp_path, asset, **kwargs):
    path = manifest(tmp_path, asset, **kwargs)
    report_path = tmp_path / "report.json"
    run = cli("review", "--manifest", path, "--output", report_path)
    assert run.returncode in (0, 1, 2), run.stderr
    return json.loads(report_path.read_text(encoding="utf-8"))


def by_algorithm(report):
    return {item["algorithm"]: item for item in report["results"]}


def test_alpha_palette_centroid_and_margin_exact_values(tmp_path):
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    image.putpixel((1, 2), (240, 10, 20, 255))
    image.putpixel((5, 3), (10, 30, 240, 128))
    asset = tmp_path / "alpha.png"
    image.save(asset)

    inspected = json.loads(cli("inspect", asset).stdout)
    assert inspected["alpha_bbox"] == [1, 2, 6, 4]
    assert inspected["transparent_ratio"] == 62 / 64

    report = review(tmp_path, asset, constraints={
        "max_normalized_centroid_offset": 0.2,
        "min_margin_ratio": 0.1,
        "max_palette_colors": 2,
    })
    rows = {row["algorithm"]: row for row in report["results"]}
    cx = (1.5 * 255 + 5.5 * 128) / 383
    cy = (2.5 * 255 + 3.5 * 128) / 383
    expected_offset = math.hypot((cx - 4) / 8, (cy - 4) / 8)
    assert rows["alpha_centroid_offset"]["measured"] == pytest.approx(expected_offset, abs=1e-12)
    assert rows["alpha_margin_ratio"]["measured"] == {
        "left": 1 / 8, "top": 2 / 8, "right": 2 / 8, "bottom": 4 / 8,
    }
    assert rows["exact_palette_count"]["measured"] == 2


def test_mask_palette_uniformity_topology_symmetry_overlap_and_luma(tmp_path):
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 255))
    shape_pixels = {(1, 1), (2, 1), (2, 2), (6, 6), (6, 7)}
    for point in shape_pixels:
        image.putpixel(point, (200, 40, 30, 255))
    image.putpixel((2, 2), (201, 40, 30, 255))
    asset = tmp_path / "features.png"
    image.save(asset)

    shape = Image.new("L", (8, 8), 0)
    for point in shape_pixels:
        shape.putpixel(point, 255)
    shape.save(tmp_path / "shape.png")
    subset = Image.new("L", (8, 8), 0)
    subset.putpixel((2, 1), 255)
    subset.putpixel((2, 2), 255)
    subset.save(tmp_path / "subset.png")
    features = [{
        "id": "shape", "role": "foreground", "geometry": {"type": "mask", "path": "shape.png"},
        "properties": {"max_connected_components": 2, "min_symmetry_iou": 0.0,
                       "min_occupancy_ratio": 5 / 64, "max_silhouette_components": 2,
                       "max_palette_colors": 2, "palette_bin_size_lab": [100, 100, 100],
                       "max_luma_levels": 2, "luma_bin_width": 128},
    }, {
        "id": "shape_exact", "role": "foreground", "geometry": {"type": "mask", "path": "shape.png"},
        "properties": {"max_palette_colors": 3},
    }, {
        "id": "solid", "role": "solid", "geometry": {"type": "mask", "path": "shape.png"},
        "properties": {"surface": "solid", "max_channel_range": 1,
                       "max_delta_e00": 1.0, "delta_e_tolerance": 0.1,
                       "reference_rgb": [200, 40, 30]},
    }, {
        "id": "solid_median", "role": "solid", "geometry": {"type": "mask", "path": "shape.png"},
        "properties": {"surface": "solid", "max_delta_e00": 1.0,
                       "delta_e_tolerance": 0.1},
    }, {
        "id": "overlap", "role": "foreground", "geometry": {"type": "mask", "path": "subset.png"},
        "properties": {},
    }]
    report = review(tmp_path, asset, features=features, overlaps=[
        {"feature_a": "shape", "feature_b": "overlap", "max_overlap_ratio": 0.99},
    ])
    rows = {(row["algorithm"], row["feature_id"]): row for row in report["results"]}
    assert rows[("mask_connected_components", "shape")]["measured"] == 2
    assert rows[("mask_reflection_iou", "shape")]["measured"] == 0.0
    assert rows[("region_occupancy", "shape")]["measured"] == 5 / 64
    assert rows[("silhouette_metrics", "shape")]["measured"] == {
        "components": 2, "bbox": [1, 1, 7, 8],
    }
    assert rows[("masked_palette_count", "shape")]["measured"] == 1
    assert rows[("masked_palette_count", "shape_exact")]["measured"] == 2
    assert rows[("masked_luma_levels", "shape")]["measured"] == 1
    assert rows[("masked_color_uniformity", "solid")]["measured"] == 1
    delta = rows[("ciede2000_uniformity", "solid")]["measured"]
    assert delta["max_delta_e00"] == pytest.approx(0.20515147, abs=1e-8)
    assert delta["exceedance_ratio"] == pytest.approx(0.2)
    median_delta = rows[("ciede2000_uniformity", "solid_median")]["measured"]
    assert median_delta["reference_rgb"] == [200, 40, 30]
    assert median_delta["max_delta_e00"] == pytest.approx(0.20515147, abs=1e-8)
    assert rows[("mask_intersection_ratio", "shape:overlap")]["measured"] == 1.0
    assert report["feature_masks"]["shape"]["sha256"] == hashlib.sha256((tmp_path / "shape.png").read_bytes()).hexdigest()


def test_mask_holes_minimum_width_and_reference_shape_metrics(tmp_path):
    asset = Image.new("RGBA", (12, 12), (255, 255, 255, 255))
    asset_path = tmp_path / "asset.png"
    asset.save(asset_path)
    candidate = Image.new("L", (12, 12), 0)
    for y in range(2, 10):
        for x in range(2, 10):
            if not (4 <= x <= 7 and 4 <= y <= 7):
                candidate.putpixel((x, y), 255)
    candidate.save(tmp_path / "candidate.png")
    reference = Image.new("L", (12, 12), 0)
    for y in range(2, 10):
        for x in range(2, 10):
            reference.putpixel((x, y), 255)
    reference.save(tmp_path / "reference.png")
    report = review(tmp_path, asset_path, features=[{
        "id": "shape", "role": "character", "geometry": {"type": "mask", "path": "candidate.png"},
        "properties": {"max_holes": 0, "min_width_px": 1, "reference_mask": "reference.png",
                       "max_boundary_distance_px": 2, "min_iou": 0.7, "min_dice": 0.8},
    }])
    rows = {row["algorithm"]: row for row in report["results"]}
    assert rows["mask_hole_count"]["measured"] == 1
    assert rows["mask_hole_count"]["status"] == "FAIL"
    assert rows["mask_minimum_width"]["measured"] == 2
    assert rows["mask_boundary_distance"]["measured"] > 0
    assert rows["mask_iou_dice"]["measured"] < 1
    assert rows["mask_dice"]["measured"] < 1


def test_minimum_width_detects_a_one_pixel_neck(tmp_path):
    from i_mage_not_ai.shape_metrics import minimum_width

    mask = np.zeros((17, 15), dtype=bool)
    mask[1:7, 3:12] = True
    mask[10:16, 3:12] = True
    mask[7:10, 7] = True
    assert minimum_width(mask) == 2


def test_skeleton_hu_shape_and_roi_edge_metrics(tmp_path):
    asset = Image.new("RGB", (16, 16), (0, 0, 0))
    for y in range(4, 12):
        for x in range(4, 12):
            asset.putpixel((x, y), (255, 255, 255))
    asset_path = tmp_path / "edge-asset.png"
    asset.save(asset_path)
    mask = Image.new("L", (16, 16), 0)
    for y in range(4, 12):
        for x in range(4, 12): mask.putpixel((x, y), 255)
    mask.save(tmp_path / "roi.png")
    reference = Image.new("L", (16, 16), 0)
    for y in range(4, 12):
        for x in range(4, 12): reference.putpixel((x, y), 255)
    reference.save(tmp_path / "ref-roi.png")
    report = review(tmp_path, asset_path, features=[{
        "id": "roi", "role": "character", "geometry": {"type": "mask", "path": "roi.png"},
        "properties": {"max_skeleton_endpoints": 4, "max_skeleton_branches": 0,
                       "reference_mask": "ref-roi.png", "max_hu_shape_distance": 0.001,
                       "min_tenengrad": 1, "min_boundary_gradient": 1},
    }])
    rows = {row["algorithm"]: row for row in report["results"]}
    assert rows["mask_skeleton_endpoints"]["measured"] == 4
    assert rows["mask_skeleton_branches"]["measured"] == 1
    assert rows["mask_hu_shape_distance"]["measured"] == pytest.approx(0)
    assert rows["roi_tenengrad_sharpness"]["measured"] > 1
    assert rows["mask_boundary_gradient"]["measured"] > 1


def test_review_and_measure_use_same_linear_srgb_luma_bins(tmp_path):
    from i_mage_not_ai.measurements import measure_manifest
    image = Image.new("RGB", (32, 1))
    image.putdata([(128, 128, 128)] * 16 + [(160, 160, 160)] * 16)
    asset = tmp_path / "luma.png"
    image.save(asset)
    mask = Image.new("L", image.size, 255)
    mask.save(tmp_path / "mask.png")
    path = manifest(tmp_path, asset, features=[{"id":"all","role":"all",
        "geometry":{"type":"mask","path":"mask.png"},
        "properties":{"max_luma_levels":2,"luma_bin_width":64}}])
    reviewed = review(tmp_path, asset, features=[{"id":"all","role":"all",
        "geometry":{"type":"mask","path":"mask.png"},
        "properties":{"max_luma_levels":2,"luma_bin_width":64}}])
    diagnostic = measure_manifest(path)
    review_count = next(row for row in reviewed["results"] if row["algorithm"]=="masked_luma_levels")["measured"]
    measured_count = diagnostic["features"][0]["metrics"]["luma_levels"]["count"]
    assert review_count == measured_count == 2


def test_review_and_measure_ignore_fully_transparent_mask_pixels(tmp_path):
    from i_mage_not_ai.measurements import measure_manifest
    image = Image.new("RGBA", (2, 1))
    image.putdata([(128, 128, 128, 255), (255, 255, 255, 0)])
    asset = tmp_path / "transparent-luma.png"
    image.save(asset)
    mask = Image.new("L", image.size, 255)
    mask.save(tmp_path / "mask.png")
    features = [{"id":"all","role":"all","geometry":{"type":"mask","path":"mask.png"},
                "properties":{"max_luma_levels":1,"luma_bin_width":64}}]
    path = manifest(tmp_path, asset, features=features)
    reviewed = review(tmp_path, asset, features=features)
    diagnostic = measure_manifest(path)
    review_count = next(row for row in reviewed["results"] if row["algorithm"]=="masked_luma_levels")["measured"]
    measure_count = diagnostic["features"][0]["metrics"]["luma_levels"]["count"]
    assert review_count == measure_count == 1


def test_tile_fft_gradient_and_raster_semantics(tmp_path):
    checker = Image.new("RGBA", (8, 8))
    for y in range(8):
        for x in range(8):
            value = 255 if (x + y) % 2 else 0
            checker.putpixel((x, y), (value, value, value, 255))
    checker.save(tmp_path / "checker.png")
    mask = Image.new("L", (8, 8), 255)
    mask.save(tmp_path / "all.png")
    features = [{"id": "background", "role": "background",
                 "geometry": {"type": "mask", "path": "all.png"},
                 "properties": {"max_periodic_energy_ratio": 0.9,
                                "min_occupancy_ratio": 1.0,
                                "max_gradient_fit_r2": 0.9,
                                "gradient_allowed": False}}]
    report = review(tmp_path, tmp_path / "checker.png", features=features)
    rows = {row["algorithm"]: row for row in report["results"]}
    assert rows["fft_periodic_energy_ratio"]["measured"] == pytest.approx(1.0, abs=1e-12)
    assert rows["region_occupancy"]["measured"] == 1.0
    assert rows["raster_gradient_semantic_check"]["status"] == "INCONCLUSIVE"
    assert rows["linear_gradient_fit_diagnostic"]["status"] == "INCONCLUSIVE"

    seam = Image.new("RGBA", (2, 2), (0, 0, 0, 255))
    seam.putpixel((1, 0), (10, 0, 0, 255))
    seam.putpixel((0, 1), (0, 20, 0, 255))
    seam.putpixel((1, 1), (10, 20, 0, 255))
    seam.save(tmp_path / "seam.png")
    seam_report = review(tmp_path, tmp_path / "seam.png", features=[{
        "id": "background", "role": "background", "properties": {"seamless_tile": True, "max_tile_edge_error": 4.0}
    }])
    tile = next(row for row in seam_report["results"] if row["algorithm"] == "tile_edge_color_error")
    assert tile["measured"] == pytest.approx(5.0)

    gradient = Image.new("RGBA", (8, 8))
    for y in range(8):
        for x in range(8):
            gradient.putpixel((x, y), (x * 20, y * 20, (x + y) * 10, 255))
    gradient.save(tmp_path / "gradient.png")
    grad_report = review(tmp_path, tmp_path / "gradient.png", features=[{
        "id": "background", "role": "background", "geometry": {"type": "mask", "path": "all.png"},
        "properties": {"max_gradient_fit_r2": 0.9},
    }])
    fit = next(row for row in grad_report["results"] if row["algorithm"] == "linear_gradient_fit_diagnostic")
    assert fit["measured"] == pytest.approx(1.0, abs=1e-12)
    assert fit["status"] == "INCONCLUSIVE"


def test_svg_counts_strokes_and_line_geometry(tmp_path):
    svg = tmp_path / "asset.svg"
    svg.write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">
      <defs><linearGradient id="a"/><radialGradient id="b"/></defs>
      <path id="diag" d="M0 0 L3 4" stroke-width="3"/>
    </svg>''', encoding="utf-8")
    doc = {"schema_version": "0.5", "asset": {"path": svg.name,
            "sha256": hashlib.sha256(svg.read_bytes()).hexdigest()},
           "constraints": {"max_svg_gradients": 1, "max_svg_stroke_width": 2},
           "features": [{"id": "diag", "role": "foreground", "properties": {
               "svg_path_id": "diag", "min_path_length": 5, "max_path_length": 5,
               "min_path_segments": 1, "max_path_aspect_ratio": 1,
           }}]}
    path = tmp_path / "svg-manifest.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = cli("review", "--manifest", path)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    rows = {row["algorithm"]: row for row in report["results"]}
    assert rows["svg_gradient_declarations"]["measured"] == 2
    assert rows["svg_stroke_width_range"]["measured"] == 3
    path_result = rows["svg_path_geometry"]["measured"]["path"]
    assert path_result["length"] == pytest.approx(5.0)
    assert path_result["bbox"] == [0.0, 0.0, 3.0, 4.0]
    assert path_result["segment_count"] == 1


def test_game_trace_motion_pose_event_particle_anchor_and_occlusion_gold_values(tmp_path):
    back = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    back.save(tmp_path / "back.png")
    front = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    front.putpixel((0, 0), (0, 0, 0, 255))
    front.save(tmp_path / "front.png")

    def track(identity, kind, values, **extra):
        return {"id": identity, "kind": kind, "units": "m" if kind != "rotation" else "quaternion",
                "coordinate_system": "world",
                "samples": [{"time": i, "value": value} for i, value in enumerate(values)], **extra}

    x = [[0, 0], [1, 0], [4, 0], [9, 0]]
    trace = {"schema_version": "1", "timebase": "seconds", "random_seed": 3,
             "tracks": [
                 track("root", "position", x),
                 track("target", "position", [[v[0] + 0.1, v[1]] for v in x]),
                 track("foot", "position", [[0, 0], [0.1, 0], [0.1, 0], [0.1, 0]]),
                 track("upper", "position", [[0, 1], [1, 1], [4, 1], [9, 1]],
                       parent_track_id="root", rest_length=1),
                 track("turn", "rotation", [[0, 0, 0, 1], [0, 0, 1, 0]]),
                 track("pose", "pose", [{"root": [0, 0], "hand": [0, 0]},
                                          {"root": [3, 4], "hand": [0, 0]}]),
             ],
             "events": [{"id": "hit", "event_type": "impact", "time": 0.75}],
             "particles": [{"id": "spark", "spawn_time": 1, "death_time": 2, "position_track_id": "root"}],
             "contacts": [{"id": "stance", "track_id": "foot", "start_time": 0, "end_time": 3}],
             "anchors": [{"id": "grip", "track_a": "root", "track_b": "target"}],
             "frames": [{"time": 0, "layers": [
                 {"id": "back", "path": "back.png", "z_index": 0},
                 {"id": "front", "path": "front.png", "z_index": 1},
             ]}],
    }
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    policy = {"max_position_step": 5, "max_velocity": 5, "max_acceleration": 2.01, "max_jerk": 0.11,
              "max_loop_boundary_difference": 9, "max_rotation_step_degrees": 180,
              "max_rotation_loop_difference_degrees": 180, "max_pose_loop_boundary_distance": 5,
              "expected_event_times": {"hit": 0.7}, "max_event_timing_error": 0.1,
              "max_particle_lifetime": 1, "max_bone_length_deviation": 0,
              "max_foot_sliding_distance": 0.11, "max_anchor_distance": 0.11,
              "particle_bounds": {"min": [-1, -1], "max": [10, 1], "units": "m", "coordinate_system": "world"},
              "max_alpha_occlusion_ratio": 0.0625}
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    result = cli("game", "analyze", "--trace", trace_path, "--policy", policy_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["completeness"] == "COMPLETE"
    rows = {row["id"]: row for row in report["results"] if row.get("id")}
    assert rows["MOTION:root"]["measured"]["max_step"] == 5
    assert rows["MOTION:root"]["measured"]["mean_step"] == 3
    assert rows["VELOCITY:root"]["measured"]["max"] == 5
    assert rows["ACCEL:root"]["measured"]["max"] == 2
    assert rows["JERK:root"]["measured"]["max"] == 0
    assert rows["ROTATION:turn"]["measured"]["max_degrees"] == 180
    assert rows["ROTATION-LOOP:turn"]["measured"]["degrees"] == 180
    assert rows["POSE-LOOP:pose"]["measured"]["distance"] == 5
    assert rows["BONE:upper"]["measured"]["max_relative_deviation"] == 0
    assert rows["FOOTSLIDE:stance"]["measured"]["distance"] == pytest.approx(0.1)
    assert rows["ANCHOR:grip"]["measured"]["max_distance"] == pytest.approx(0.1)
    assert rows["LOOP:root"]["measured"]["distance"] == 9
    assert rows["EVENT:hit"]["measured"]["absolute_error"] == pytest.approx(0.05)
    assert rows["PARTICLE:spark"]["measured"]["lifetime"] == 1
    assert rows["BOUNDS:spark"]["measured"]["out_of_bounds_sample_count"] == 0
    assert rows["OCCLUSION:0:back"]["measured"]["occlusion_ratio"] == 0.0625



def test_runtime_evidence_callbacks_and_particle_gold_values(tmp_path):
    evidence = {
        "format": "i_mage_not_ai.runtime-evidence/1", "engine": "godot",
        "engine_version": "4.6.3", "timebase": "seconds", "random_seed": 7,
        "callbacks": [{"name": "impact", "time": 0.49},
                       {"name": "impact", "time": 0.51}],
        "particles": [{"emitter": "spark", "sample_time": 1.02,
                       "bounds": [-0.5, -0.25, 0.75, 0.5], "bounds_units": "m",
                       "alive_count": 8}],
    }
    policy = {
        "callbacks": {"impact": {"count": 2, "time": 0.5, "tolerance": 0.021}},
        "particles": {"spark": {"sample_time": 1, "time_tolerance": 0.021,
                                  "bounds": [-1, -1, 1, 1],
                                  "min_alive_count": 6, "max_alive_count": 10}},
    }
    evidence_path = tmp_path / "runtime-evidence.json"
    policy_path = tmp_path / "runtime-policy.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    result = cli("game", "runtime-check", "--evidence", evidence_path,
                 "--policy", policy_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["completeness"] == "COMPLETE"
    rows = {row["id"]: row for row in report["results"]}
    assert rows["CALLBACK:impact:COUNT"]["measured"]["count"] == 2
    assert rows["CALLBACK:impact:TIME"]["measured"]["max_absolute_error"] == pytest.approx(0.01)
    assert rows["PARTICLE:spark:BOUNDS"]["measured"]["bounds"] == [-0.5, -0.25, 0.75, 0.5]
    assert rows["PARTICLE:spark:COUNT"]["measured"]["alive_count"] == 8
