"""Manifest validation and deterministic raster/SVG measurements."""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
import numpy as np
from scipy import ndimage
from pathlib import Path
from typing import Any

from PIL import Image
from svgpathtools import parse_path
import yaml
from .adapters import ADAPTERS, FEATURE_ADAPTERS
from .policies import ProjectRule, match_project_rules, apply_project_rules
from .shape_metrics import compare_masks, hu_shape_distance, minimum_width, roi_edge_metrics, skeleton_graph, topology

RULES = [
    {"id": "IMAGE-001", "algorithm": "image_metadata", "description": "Verify asset readability, dimensions, and optional SHA-256."},
    {"id": "ALPHA-001", "algorithm": "alpha_occupancy", "description": "Measure transparent pixel fraction and occupied bounding box."},
    {"id": "GEOMETRY-001", "algorithm": "alpha_centroid_offset", "description": "Measure occupied alpha centroid offset from image center."},
    {"id": "GEOMETRY-002", "algorithm": "alpha_margin_ratio", "description": "Measure normalized transparent margins around the alpha bounds."},
    {"id": "COLOR-001", "algorithm": "exact_palette_count", "description": "Count distinct visible RGB colors (deterministic exact count)."},
    {"id": "COLOR-002", "algorithm": "masked_palette_count", "description": "Count exact visible RGB colors inside a feature mask."},
    {"id": "TOPOLOGY-001", "algorithm": "mask_connected_components", "description": "Count 8-connected components in a declared binary feature mask."},
    {"id": "TOPOLOGY-002", "algorithm": "mask_hole_count", "description": "Count enclosed 4-connected holes in a declared binary feature mask."},
    {"id": "TOPOLOGY-003", "algorithm": "mask_skeleton_endpoints", "description": "Count endpoints in a morphological feature-mask skeleton."},
    {"id": "TOPOLOGY-004", "algorithm": "mask_skeleton_branches", "description": "Count branch clusters in a morphological feature-mask skeleton."},
    {"id": "GEOMETRY-003", "algorithm": "mask_minimum_width", "description": "Estimate minimum local mask width from the distance transform on a thinned skeleton."},
    {"id": "GEOMETRY-004", "algorithm": "mask_boundary_distance", "description": "Measure symmetric Hausdorff distance between candidate and reference mask boundaries in pixels."},
    {"id": "GEOMETRY-005", "algorithm": "mask_iou_dice", "description": "Compare candidate and reference masks using intersection over union."},
    {"id": "GEOMETRY-006", "algorithm": "mask_dice", "description": "Compare candidate and reference masks using Dice coefficient."},
    {"id": "GEOMETRY-007", "algorithm": "mask_hu_shape_distance", "description": "Compare Hu moment shape descriptors against a reference mask."},
    {"id": "RASTER-EDGE-001", "algorithm": "roi_tenengrad_sharpness", "description": "Measure mean squared Sobel gradient within a declared feature mask."},
    {"id": "RASTER-EDGE-002", "algorithm": "mask_boundary_gradient", "description": "Measure mean Sobel gradient magnitude along the declared feature-mask boundary."},
    {"id": "SYMMETRY-001", "algorithm": "mask_reflection_iou", "description": "Compare a declared feature mask with its horizontal reflection."},
    {"id": "BACKGROUND-001", "algorithm": "tile_edge_color_error", "description": "Measure exact RGB discontinuity between opposing tile edges."},
    {"id": "BACKGROUND-002", "algorithm": "fft_periodic_energy_ratio", "description": "Measure non-DC FFT energy concentrated at periodic peaks in a masked region."},
    {"id": "BACKGROUND-003", "algorithm": "region_occupancy", "description": "Measure feature-mask area as a fraction of the canvas."},
    {"id": "CHARACTER-001", "algorithm": "masked_luma_levels", "description": "Count occupied quantized luminance bands inside a feature mask."},
    {"id": "CHARACTER-002", "algorithm": "silhouette_metrics", "description": "Measure silhouette area, bounds, and 8-connected components."},
    {"id": "CHARACTER-003", "algorithm": "openpose_bone_ratio", "description": "Compare OpenPose BODY_25 segment lengths with an explicit normalized range."},
    {"id": "CHARACTER-004", "algorithm": "openpose_joint_angle", "description": "Compare OpenPose BODY_25 joint angles with explicit angular ranges."},
    {"id": "SIMILARITY-001", "algorithm": "lpips_similarity", "description": "Measure aligned reference-image perceptual distance using local LPIPS weights."},
    {"id": "SIMILARITY-002", "algorithm": "dinov2_cosine_similarity", "description": "Measure cosine similarity of local DINOv2 CLS embeddings."},
    {"id": "GRADIENT-002", "algorithm": "linear_gradient_fit_diagnostic", "description": "Fit an RGB plane and report R-squared as diagnostic evidence only."},
    {"id": "GRADIENT-001", "algorithm": "raster_gradient_semantic_check", "description": "Raster data cannot prove semantic gradient intent; return INCONCLUSIVE."},
    {"id": "SOLID-001", "algorithm": "masked_color_uniformity", "description": "Measure RGB channel range over a declared solid feature mask."},
    {"id": "SOLID-002", "algorithm": "ciede2000_uniformity", "description": "Measure Lab ΔE00 and exceedance ratio against an explicit or median reference."},
    {"id": "SVG-001", "algorithm": "svg_gradient_declarations", "description": "Count linearGradient and radialGradient elements in SVG source."},
    {"id": "SVG-002", "algorithm": "svg_stroke_width_range", "description": "Measure declared SVG stroke-width values."},
    {"id": "SVG-003", "algorithm": "svg_path_geometry", "description": "Measure parsed SVG path bounds, length, and segment types."},
]
_PACKS_FILE=Path(__file__).parent/"rules"/"packs.yaml"
_ALGORITHMS_FILE=Path(__file__).parent/"rules"/"algorithms.yaml"
class _UniqueKeyLoader(yaml.SafeLoader): pass
def _unique_mapping(loader,node,deep=False):
    mapping={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in mapping: raise ValueError(f"duplicate YAML key: {key}")
        mapping[key]=loader.construct_object(value_node,deep=deep)
    return mapping
_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_unique_mapping)
try:
    _PACKS_DATA=yaml.load(_PACKS_FILE.read_text(encoding="utf-8"),Loader=_UniqueKeyLoader)
    if _PACKS_DATA.get("schema_version")!=1 or not isinstance(_PACKS_DATA.get("packs"),dict):
        raise ValueError("unsupported rule pack registry")
    RULE_PACKS=_PACKS_DATA["packs"]
    _ALGORITHM_DATA=yaml.load(_ALGORITHMS_FILE.read_text(encoding="utf-8"),Loader=_UniqueKeyLoader)
    ALGORITHM_REGISTRY=_ALGORITHM_DATA["algorithms"]
    if _ALGORITHM_DATA.get("schema_version")!=1 or not isinstance(ALGORITHM_REGISTRY,dict): raise ValueError("unsupported algorithm registry")
    if len(ALGORITHM_REGISTRY)!=len(set(ALGORITHM_REGISTRY)): raise ValueError("duplicate algorithm identifiers")
    unknown={algorithm for pack in RULE_PACKS.values() for algorithm in pack.get("algorithms",[])}-set(ALGORITHM_REGISTRY)
    if unknown: raise ValueError(f"rule packs reference unknown algorithms: {sorted(unknown)}")
except (OSError, yaml.YAMLError, AttributeError, ValueError) as exc:
    raise RuntimeError(f"cannot load rule pack registry: {exc}") from exc


def _linear_srgb_luma(pixels: np.ndarray) -> np.ndarray:
    """Return linear-sRGB luminance on a 0..255 scale for RGB rows."""
    srgb = np.asarray(pixels, dtype=np.float64) / 255.0
    linear = np.where(srgb > .04045, ((srgb + .055) / 1.055) ** 2.4, srgb / 12.92)
    return (linear @ np.array([.2126, .7152, .0722])) * 255.0
ALGORITHMS = {item["algorithm"]: item for item in RULES}
ADAPTER_CAPABILITIES = {key:set(adapter.capabilities) for key,adapter in ADAPTERS.items()}


class ManifestError(ValueError):
    pass


def _safe_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ManifestError(f"path must be relative to the manifest: {value}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ManifestError(f"path escapes the manifest directory: {value}")
    if not resolved.is_file():
        raise ManifestError(f"file does not exist: {value}")
    return resolved


def load_manifest(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") not in {"0.1", "0.5", "0.6"}:
        raise ManifestError("schema_version must be '0.6' (legacy '0.1' and '0.5' are also accepted)")
    asset = data.get("asset")
    if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
        raise ManifestError("asset.path is required")
    if data["schema_version"] in {"0.5", "0.6"} and not asset.get("sha256"):
        raise ManifestError(f"schema_version {data['schema_version']} requires asset.sha256")
    resolved_asset = _safe_path(path.parent, asset["path"])
    if resolved_asset.stat().st_size > 50 * 1024 * 1024:
        raise ManifestError("asset exceeds 50 MiB safety limit")
    if asset.get("sha256") and hashlib.sha256(resolved_asset.read_bytes()).hexdigest() != asset["sha256"].lower():
        raise ManifestError("asset SHA-256 does not match manifest")
    if asset.get("format") and asset["format"].lower().lstrip(".") != resolved_asset.suffix.lower().lstrip("."):
        raise ManifestError("asset.format does not match file extension")
    if resolved_asset.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".json"}:
        raise ManifestError(f"unsupported asset format: {resolved_asset.suffix or '<none>'}")
    is_game_trace=resolved_asset.suffix.lower()==".json" and isinstance(data.get("intent"),dict) and data["intent"].get("kind")=="game_trace"
    if resolved_asset.suffix.lower()==".json" and not is_game_trace:
        raise ManifestError("JSON assets require intent.kind='game_trace'")
    if is_game_trace:
        from .game import load_trace, validate_policy
        try:
            trace=load_trace(resolved_asset)
            expected_engine=asset.get("engine")
            if expected_engine not in (None,"generic","godot","unity"): raise ValueError("asset.engine must be generic, godot, or unity")
            if expected_engine and expected_engine!=trace.engine: raise ValueError(f"manifest engine {expected_engine} does not match trace engine {trace.engine}")
            if expected_engine is None: asset["engine"]=trace.engine
            validate_policy(data.get("game_policy",{}))
            if data.get("schema_version") == "0.6" and (not isinstance(data.get("scenario_id"),str) or not data["scenario_id"].strip()):
                raise ValueError("schema_version 0.6 game traces require a non-empty scenario_id")
        except ValueError as exc: raise ManifestError(str(exc)) from exc
    if "features" not in data:
        raise ManifestError("features is required")
    features = data["features"]
    if not isinstance(features, list):
        raise ManifestError("features must be an array")
    constraints = data.get("constraints", {})
    if not isinstance(constraints, dict): raise ManifestError("constraints must be an object")
    regression=data.get("regression",{})
    if not isinstance(regression,dict): raise ManifestError("regression must be an object")
    region=regression.get("allowed_region")
    if region is not None:
        if not isinstance(region,list) or len(region)!=4 or any(not isinstance(v,int) or isinstance(v,bool) for v in region):
            raise ManifestError("regression.allowed_region must be [x1,y1,x2,y2]")
        if not (0<=region[0]<region[2] and 0<=region[1]<region[3]): raise ManifestError("regression.allowed_region bounds are invalid")
    allowed_mask_path=None
    if regression.get("allowed_mask") is not None:
        if not isinstance(regression["allowed_mask"],str): raise ManifestError("regression.allowed_mask must be a relative path")
        allowed_mask_path=_safe_path(path.parent,regression["allowed_mask"])
    ids: set[str] = set()
    parents: dict[str, str] = {}
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("id"), str) or not feature["id"]:
            raise ManifestError("each feature requires a non-empty string id")
        if feature["id"] in ids:
            raise ManifestError(f"duplicate feature id: {feature['id']}")
        ids.add(feature["id"])
        if feature.get("parent_id") is not None:
            if not isinstance(feature["parent_id"], str): raise ManifestError(f"feature {feature['id']}: parent_id must be a string")
            parents[feature["id"]] = feature["parent_id"]
        geometry = feature.get("geometry", {})
        if geometry and not isinstance(geometry, dict):
            raise ManifestError(f"feature {feature['id']}: geometry must be an object")
        if geometry.get("type") == "mask":
            mask_path=_safe_path(path.parent, geometry.get("path", ""))
            expected_mask_hash=geometry.get("sha256")
            if expected_mask_hash and hashlib.sha256(mask_path.read_bytes()).hexdigest() != expected_mask_hash.lower():
                raise ManifestError(f"feature {feature['id']} mask SHA-256 does not match manifest")
            if allowed_mask_path and mask_path==allowed_mask_path: raise ManifestError("regression mask cannot be reused as a feature mask")
        properties = feature.get("properties", {})
        if not isinstance(properties, dict): raise ManifestError(f"feature {feature['id']}: properties must be an object")
        if "reference_mask" in properties:
            if not isinstance(properties["reference_mask"], str): raise ManifestError(f"feature {feature['id']}.reference_mask must be a relative path")
            _safe_path(path.parent, properties["reference_mask"])
        if any(key in properties for key in ("max_boundary_distance_px", "min_iou", "min_dice")) and "reference_mask" not in properties:
            raise ManifestError(f"feature {feature['id']}: reference_mask is required for boundary-distance/IoU/Dice checks")
        if "max_hu_shape_distance" in properties and "reference_mask" not in properties:
            raise ManifestError(f"feature {feature['id']}: reference_mask is required for Hu shape comparison")
        if "max_holes" in properties and (not isinstance(properties["max_holes"], int) or isinstance(properties["max_holes"], bool) or properties["max_holes"] < 0):
            raise ManifestError(f"feature {feature['id']}.max_holes must be a non-negative integer")
        if "min_width_px" in properties and properties["min_width_px"] <= 0:
            raise ManifestError(f"feature {feature['id']}.min_width_px must be greater than zero")
        for key in ("min_iou", "min_dice"):
            if key in properties and not 0 <= properties[key] <= 1:
                raise ManifestError(f"feature {feature['id']}.{key} must be between zero and one")
        if "luma_bin_width" in properties:
            width=properties["luma_bin_width"]
            if not isinstance(width,(int,float)) or isinstance(width,bool) or not math.isfinite(width) or width<=0:
                raise ManifestError(f"feature {feature['id']}.luma_bin_width must be a finite number greater than zero")
        provenance=feature.get("provenance",feature.get("evidence",{}))
        if provenance:
            if not isinstance(provenance,dict) or provenance.get("origin") not in {"user_spec","project_config","source_native","agent_inferred"}:
                raise ManifestError(f"feature {feature['id']}: provenance origin is invalid")
    for scope, values in (("constraints", constraints), *((f"feature {f['id']} properties", f.get("properties", {})) for f in features)):
        for key, value in values.items():
            if key.startswith(("max_", "min_")) and (not isinstance(value, (int,float)) or isinstance(value, bool) or not math.isfinite(value)):
                raise ManifestError(f"{scope}.{key} must be a finite number")
    for fid, parent in parents.items():
        if parent not in ids: raise ManifestError(f"feature {fid}: unknown parent_id {parent}")
        seen={fid}; cursor=parent
        while cursor in parents:
            if cursor in seen: raise ManifestError(f"feature hierarchy contains a cycle at {cursor}")
            seen.add(cursor); cursor=parents[cursor]
    overlaps=data.get("overlap_constraints", [])
    if not isinstance(overlaps,list): raise ManifestError("overlap_constraints must be an array")
    for item in overlaps:
        if not isinstance(item,dict) or item.get("feature_a") not in ids or item.get("feature_b") not in ids:
            raise ManifestError("each overlap constraint must reference known feature_a and feature_b ids")
        limit=item.get("max_overlap_ratio")
        if not isinstance(limit,(int,float)) or isinstance(limit,bool) or not math.isfinite(limit) or not 0 <= limit <= 1:
            raise ManifestError("overlap max_overlap_ratio must be between 0 and 1")
    from pydantic import ValidationError
    from .models import FeatureManifest
    try: FeatureManifest.model_validate(data)
    except ValidationError as exc: raise ManifestError(f"manifest model validation failed: {exc.errors(include_url=False)}") from exc
    if resolved_asset.suffix.lower() not in {".svg",".json"}:
        try:
            with Image.open(resolved_asset) as source: asset_size=source.size
            mask_paths=[_safe_path(path.parent,f["geometry"]["path"]) for f in features if f.get("geometry",{}).get("type")=="mask"]
            mask_paths.extend(_safe_path(path.parent,f["properties"]["reference_mask"]) for f in features if "reference_mask" in f.get("properties",{}))
            if allowed_mask_path: mask_paths.append(allowed_mask_path)
            for mask_path in mask_paths:
                with Image.open(mask_path) as mask:
                    if mask.size!=asset_size: raise ManifestError(f"mask dimensions {mask.size} do not match asset dimensions {asset_size}: {mask_path.name}")
        except OSError as exc: raise ManifestError(f"cannot inspect asset or mask dimensions: {exc}") from exc
    return data, path.parent


def asset_path(manifest: dict[str, Any], root: Path) -> Path:
    return _safe_path(root, manifest["asset"]["path"])


def resolve_rules(manifest: dict[str, Any], project_rules: list[ProjectRule] | None = None) -> list[dict[str, Any]]:
    """Deterministically map declared constraints to the fixed algorithm registry."""
    if manifest.get("intent",{}).get("kind")=="game_trace":
        policy=manifest.get("game_policy",{})
        game_rules: list[dict[str, Any]] = [{"rule_id":"GAME-POSITION-DIFFERENCE","feature_id":None,"algorithm":"position_difference"}]
        for key,algorithm in (("max_position_step","position_difference"),("max_velocity","velocity_magnitude"),
                ("max_acceleration","acceleration_magnitude"),("max_jerk","jerk_magnitude"),
                ("max_loop_boundary_difference","loop_boundary_difference"),("expected_event_times","event_timing_difference"),
                ("max_event_timing_error","event_timing_difference"),("max_particle_lifetime","particle_lifetime"),
                ("max_bone_length_deviation","bone_length_deviation"),("max_foot_sliding_distance","foot_sliding_distance"),
                ("particle_bounds","particle_emitter_bounds"),("max_alpha_occlusion_ratio","alpha_weighted_occlusion"),
                ("max_pose_loop_boundary_distance","pose_boundary_distance"),("max_rotation_step_degrees","rotation_distance"),
                ("max_rotation_loop_difference_degrees","rotation_distance"),("max_anchor_distance","anchor_distance")):
            if key in policy and not any(item["algorithm"]==algorithm for item in game_rules):
                game_rules.append({"rule_id":f"GAME-{algorithm.upper()}","feature_id":None,"algorithm":algorithm})
        return game_rules
    selected: list[dict[str, Any]] = [{"rule_id":"IMAGE-001", "feature_id":None, "algorithm":"image_metadata"}]
    c = manifest.get("constraints", {})
    for key, rule_id, algorithm in (("max_palette_colors","COLOR-001","exact_palette_count"),
        ("min_margin_ratio","GEOMETRY-002","alpha_margin_ratio"),
        ("max_normalized_centroid_offset","GEOMETRY-001","alpha_centroid_offset"),
        ("max_svg_gradients","SVG-001","svg_gradient_declarations"),
        ("max_svg_stroke_width","SVG-002","svg_stroke_width_range")):
        if key in c: selected.append({"rule_id":rule_id,"feature_id":None,"algorithm":algorithm})
    for f in manifest.get("features", []):
        p = f.get("properties", {})
        for key, rule_id, algorithm in (("max_channel_range","SOLID-001","masked_color_uniformity"),
            ("max_palette_colors","COLOR-002","masked_palette_count"),
            ("max_connected_components","TOPOLOGY-001","mask_connected_components"),
            ("min_symmetry_iou","SYMMETRY-001","mask_reflection_iou"),
            ("max_periodic_energy_ratio","BACKGROUND-002","fft_periodic_energy_ratio"),
            ("max_luma_levels","CHARACTER-001","masked_luma_levels"),
            ("max_silhouette_components","CHARACTER-002","silhouette_metrics"),
            ("min_occupancy_ratio","BACKGROUND-003","region_occupancy"),
            ("max_holes","TOPOLOGY-002","mask_hole_count"),
            ("max_skeleton_endpoints","TOPOLOGY-003","mask_skeleton_endpoints"),
            ("max_skeleton_branches","TOPOLOGY-004","mask_skeleton_branches"),
            ("min_width_px","GEOMETRY-003","mask_minimum_width"),
            ("max_boundary_distance_px","GEOMETRY-004","mask_boundary_distance"),
            ("min_iou","GEOMETRY-005","mask_iou_dice"),
            ("min_dice","GEOMETRY-006","mask_dice"),
            ("max_hu_shape_distance","GEOMETRY-007","mask_hu_shape_distance"),
            ("min_tenengrad","RASTER-EDGE-001","roi_tenengrad_sharpness"),
            ("min_boundary_gradient","RASTER-EDGE-002","mask_boundary_gradient")):
            if key in p: selected.append({"rule_id":rule_id,"feature_id":f["id"],"algorithm":algorithm})
        if p.get("surface")=="solid" and ("max_delta_e00" in p or "max_exceedance_ratio" in p):
            selected.append({"rule_id":"SOLID-002","feature_id":f["id"],"algorithm":"ciede2000_uniformity"})
        if "max_gradient_fit_r2" in p: selected.append({"rule_id":"GRADIENT-002","feature_id":f["id"],"algorithm":"linear_gradient_fit_diagnostic"})
        if p.get("max_svg_stroke_width") is not None:
            selected.append({"rule_id":"SVG-002","feature_id":f["id"],"algorithm":"svg_stroke_width_range"})
        if any(k in p for k in ("max_path_length","min_path_length","min_path_segments","max_path_aspect_ratio")):
            selected.append({"rule_id":"SVG-003","feature_id":f["id"],"algorithm":"svg_path_geometry"})
        if p.get("seamless_tile") is True: selected.append({"rule_id":"BACKGROUND-001","feature_id":f["id"],"algorithm":"tile_edge_color_error"})
        if p.get("gradient_allowed") is False:
            svg = Path(manifest["asset"]["path"]).suffix.lower() == ".svg"
            selected.append({"rule_id":"SVG-001" if svg else "GRADIENT-001", "feature_id":f["id"],
                             "algorithm":"svg_gradient_declarations" if svg else "raster_gradient_semantic_check"})
    for item in manifest.get("overlap_constraints",[]):
        selected.append({"rule_id":"OVERLAP-001", "feature_id":f"{item['feature_a']}:{item['feature_b']}",
                         "feature_ids":[item["feature_a"],item["feature_b"]],"algorithm":"mask_intersection_ratio"})
    for rule,feature_id in match_project_rules(manifest,project_rules or []):
        if rule.algorithm.id == "mask_intersection_ratio":
            pair = rule.algorithm.parameters.get("features", [])
            if len(pair) == 2:
                feature_id = f"{pair[0]}:{pair[1]}"
        pair=rule.algorithm.parameters.get("features",[]) if rule.algorithm.id=="mask_intersection_ratio" else []
        selected.append({"rule_id":rule.id,"feature_id":feature_id,"feature_ids":pair,"algorithm":rule.algorithm.id,"algorithm_version":rule.algorithm.version,
                         "severity":rule.severity,"priority":rule.priority,"on_missing":rule.on_missing,"required_capabilities":rule.requires})
    return selected


def plan_review(manifest: dict[str, Any], project_rules: list[ProjectRule] | None = None) -> dict[str, Any]:
    """Build a deterministic capability plan without reading or changing the asset."""
    fmt=Path(manifest["asset"]["path"]).suffix.lower()
    engine=manifest.get("asset",{}).get("engine","generic")
    source=f"{engine}_trace" if manifest.get("intent",{}).get("kind")=="game_trace" and engine in {"godot","unity"} else ("game_trace" if manifest.get("intent",{}).get("kind")=="game_trace" else ("svg" if fmt==".svg" else "raster"))
    checks=[]; missing=[]; missing_data=[]
    for rule in resolve_rules(manifest,project_rules):
        spec=ALGORITHM_REGISTRY.get(rule["algorithm"])
        if spec is None:
            spec={"capabilities":[],"adapter":"unregistered","version":"unknown"}
        requested_version=rule.get("algorithm_version")
        available_version=spec["version"]
        if requested_version is None or requested_version==available_version:
            version_supported=True
        else:
            try:
                requested_parts=[int(part) for part in requested_version.split(".")]
                available_parts=[int(part) for part in available_version.split(".")]
                while requested_parts and requested_parts[-1]==0: requested_parts.pop()
                while available_parts and available_parts[-1]==0: available_parts.pop()
                version_supported=requested_parts==available_parts
            except (AttributeError,ValueError):
                version_supported=False
        adapter=source if spec["adapter"] in {"source","game_trace"} and source in {"game_trace","godot_trace","unity_trace"} else (source if spec["adapter"]=="source" else spec["adapter"])
        supported=adapter==source and adapter in ADAPTER_CAPABILITIES and all(cap in ADAPTER_CAPABILITIES[adapter] for cap in spec["capabilities"])
        if not supported: missing.extend(spec["capabilities"])
        required=sorted(set(spec["capabilities"])|set(rule.get("required_capabilities",[])))
        supported &= all(cap in ADAPTER_CAPABILITIES.get(adapter,set()) for cap in required)
        if not supported: missing.extend(required)
        state="UNSUPPORTED_VERSION" if not version_supported else ("READY" if supported else "MISSING_CAPABILITY")
        if not version_supported:
            missing_data.append({"rule_id":rule["rule_id"],"requested_version":requested_version,"available_version":spec["version"]})
        feature=next((f for f in manifest.get("features",[]) if f["id"]==rule.get("feature_id")),None)
        feature_adapter_ids=[]
        if supported and version_supported and rule["algorithm"] == "mask_intersection_ratio":
            pair_ids = rule.get("feature_ids", [])
            by_id = {item["id"]: item for item in manifest.get("features", [])}
            for feature_id in pair_ids:
                paired = by_id.get(feature_id)
                feature_adapter = FEATURE_ADAPTERS["mask"]
                if paired is None or not feature_adapter.supports(paired, {"feature_mask"}):
                    state = "MISSING_DATA"
                    missing_data.append({"rule_id":rule["rule_id"],"feature_id":feature_id,"required":"feature_mask"})
                else:
                    feature_adapter_ids.append(feature_adapter.id)
        if supported and version_supported and feature and "feature_mask" in required:
            feature_adapter=FEATURE_ADAPTERS["mask"]
            if feature_adapter.supports(feature,set(required) & set(feature_adapter.capabilities)): feature_adapter_ids.append(feature_adapter.id)
            else:
                state="MISSING_DATA"; missing_data.append({"rule_id":rule["rule_id"],"feature_id":feature["id"],"required":"feature_mask"})
        checks.append({**rule,"adapter_ids":[adapter],"feature_adapter_ids":feature_adapter_ids,"algorithm_version":spec["version"],
                       "requested_algorithm_version":requested_version,"required_capabilities":required,"status":state})
    return {"asset_path":manifest["asset"]["path"],"checks":checks,"missing_capabilities":sorted(set(missing)),"missing_data":missing_data,
            "status":"READY" if not missing and not missing_data else "INCOMPLETE"}


def inspect_image(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 50 * 1024 * 1024: raise ManifestError("asset exceeds 50 MiB safety limit")
    adapter=next((adapter for adapter in ADAPTERS.values() if adapter.id in {"raster","svg"} and adapter.supports(path,set())),None)
    if adapter is None: raise ManifestError(f"unsupported asset format: {path.suffix}")
    try: adapter_evidence=adapter.extract(path,set())
    except Exception as exc: raise ManifestError(f"source adapter {adapter.id} could not extract evidence: {exc}") from exc
    digest = adapter_evidence["sha256"]
    if path.suffix.lower() == ".svg":
        try:
            xml = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ManifestError(f"invalid SVG: {exc}") from exc
        counts = {"linear": 0, "radial": 0}
        elements: dict[str, int] = {}
        strokes = []
        paths: list[dict[str, Any]] = []
        parents={child:parent for parent in xml.iter() for child in parent}
        for node in xml.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag.lower() == "script": raise ManifestError("SVG scripts are not allowed")
            for key,value in node.attrib.items():
                local=key.rsplit("}",1)[-1].lower()
                if local == "href" and (":" in value or value.startswith("//")):
                    raise ManifestError("external SVG references are not allowed")
                if local=="style" and re.search(r"url\(\s*['\"]?(?!#)[^)]*://",value,re.I):
                    raise ManifestError("external SVG style references are not allowed")
            elements[tag] = elements.get(tag, 0) + 1
            if tag == "linearGradient": counts["linear"] += 1
            if tag == "radialGradient": counts["radial"] += 1
            raw = node.attrib.get("stroke-width")
            if raw:
                try: strokes.append(float(raw.removesuffix("px")))
                except ValueError: pass
            if tag == "path":
                try:
                    parsed = parse_path(node.attrib.get("d", ""))
                    xmin, xmax, ymin, ymax = parsed.bbox()
                    current: ET.Element | None=node
                    transformed=False
                    while current is not None:
                        if current.attrib.get("transform"):
                            transformed=True
                            break
                        current=parents.get(current)
                    paths.append({"id":node.attrib.get("id"), "index":len(paths),
                        "bbox":[float(xmin),float(ymin),float(xmax),float(ymax)],
                        "width":float(xmax-xmin), "height":float(ymax-ymin),
                        "length":float(parsed.length()), "segment_count":len(parsed),
                        "segment_types":[type(segment).__name__ for segment in parsed],
                        "transform":"path or ancestor transform" if transformed else None})
                except Exception as exc:
                    raise ManifestError(f"invalid SVG path #{len(paths)}: {exc}") from exc
        return {"path": str(path), "format": "SVG", "sha256": digest, "adapter":adapter.id,"adapter_version":adapter.version,"gradient_declarations": counts,
                "element_counts": elements, "viewBox": xml.attrib.get("viewBox"), "explicit_stroke_widths": strokes,
                "paths":paths}
    try:
        with Image.open(path) as im:
            if im.width * im.height > 50_000_000: raise ManifestError("image exceeds 50 million pixel safety limit")
            im.load()
            alpha_present = "A" in im.getbands() or "transparency" in im.info
            rgba = im.convert("RGBA")
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox()
            return {"path": str(path), "format": im.format, "width": im.width, "height": im.height,"adapter":adapter.id,"adapter_version":adapter.version,
                    "mode": im.mode, "alpha_present":alpha_present, "sha256": digest,
                    "transparent_ratio": 1.0 - sum(alpha.histogram()[1:]) / (im.width * im.height),
                    "alpha_bbox": list(bbox) if bbox else None}
    except Exception as exc:
        raise ManifestError(f"cannot decode image: {exc}") from exc


def _rule_result(rule_id: str, feature_id: str | None, algorithm: str, measured: Any,
                 threshold: Any, status: str, formula: str, region: Any = None) -> dict[str, Any]:
    return {"rule_id": rule_id, "feature_id": feature_id, "algorithm": algorithm,
            "status": status, "measured": measured, "threshold": threshold,
            "formula": formula, "region": region}


def _bbox_list(image: Image.Image | None) -> list[int] | None:
    if image is None:
        return None
    bbox = image.getbbox()
    return list(bbox) if bbox is not None else None


def _mask_metrics(mask: Image.Image) -> tuple[int, float, float]:
    """Return 8-connected component count and foreground centroid (pixel centers)."""
    active = np.asarray(mask) > 0
    labels, count = ndimage.label(active, structure=np.ones((3, 3), dtype=np.uint8))
    del labels
    area = int(np.count_nonzero(active))
    if not area: return 0, 0.0, 0.0
    ys, xs = np.nonzero(active)
    return int(count), float(xs.mean() + .5), float(ys.mean() + .5)


def _rgb_lab(rgb):
    channels=[]
    for value in rgb:
        v=value/255
        channels.append(((v+0.055)/1.055)**2.4 if v>0.04045 else v/12.92)
    r,g,b=channels
    x=(r*.4124564+g*.3575761+b*.1804375)/.95047
    y=r*.2126729+g*.7151522+b*.0721750
    z=(r*.0193339+g*.1191920+b*.9503041)/1.08883
    f=lambda t: t**(1/3) if t>.008856451679 else 7.787037037*t+16/116
    fx,fy,fz=f(x),f(y),f(z)
    return (116*fy-16,500*(fx-fy),200*(fy-fz))


def _delta_e_2000(lab1,lab2):
    """CIEDE2000 color difference (ΔE00)."""
    L1,a1,b1=lab1; L2,a2,b2=lab2
    c1=math.hypot(a1,b1); c2=math.hypot(a2,b2); cm=(c1+c2)/2
    g=.5*(1-math.sqrt(cm**7/(cm**7+25**7)))
    ap1=(1+g)*a1; ap2=(1+g)*a2
    cp1=math.hypot(ap1,b1); cp2=math.hypot(ap2,b2)
    hp1=math.degrees(math.atan2(b1,ap1))%360; hp2=math.degrees(math.atan2(b2,ap2))%360
    dL=L2-L1; dC=cp2-cp1; dh=hp2-hp1
    if cp1*cp2==0: dh=0
    elif dh>180: dh-=360
    elif dh < -180: dh+=360
    dH=2*math.sqrt(cp1*cp2)*math.sin(math.radians(dh/2))
    lm=(L1+L2)/2; cpm=(cp1+cp2)/2
    if cp1*cp2==0: hpm=hp1+hp2
    elif abs(hp1-hp2)<=180: hpm=(hp1+hp2)/2
    elif hp1+hp2<360: hpm=(hp1+hp2+360)/2
    else: hpm=(hp1+hp2-360)/2
    t=1-.17*math.cos(math.radians(hpm-30))+.24*math.cos(math.radians(2*hpm))+.32*math.cos(math.radians(3*hpm+6))-.20*math.cos(math.radians(4*hpm-63))
    sl=1+.015*(lm-50)**2/math.sqrt(20+(lm-50)**2); sc=1+.045*cpm; sh=1+.015*cpm*t
    rt=-2*math.sqrt(cpm**7/(cpm**7+25**7))*math.sin(math.radians(60*math.exp(-((hpm-275)/25)**2)))
    return math.sqrt((dL/sl)**2+(dC/sc)**2+(dH/sh)**2+rt*(dC/sc)*(dH/sh))


def _rgb_array_lab(rgb: np.ndarray) -> np.ndarray:
    """Vectorized sRGB D65 to CIE Lab conversion for an Nx3 uint8 array."""
    srgb = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(srgb > 0.04045, ((srgb + 0.055) / 1.055) ** 2.4, srgb / 12.92)
    r, g, b = linear.T
    x = (r * .4124564 + g * .3575761 + b * .1804375) / .95047
    y = r * .2126729 + g * .7151522 + b * .0721750
    z = (r * .0193339 + g * .1191920 + b * .9503041) / 1.08883
    def f(value):
        return np.where(value > .008856451679, np.cbrt(value), 7.787037037 * value + 16 / 116)
    fx, fy, fz = f(x), f(y), f(z)
    return np.stack((116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)), axis=1)


def _delta_e_2000_array(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Vectorized CIEDE2000; inputs are Nx3 and either Nx3 or one 3-vector."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    l1, a1, b1 = lab1.T
    l2, a2, b2 = np.broadcast_to(lab2, lab1.shape).T
    c1 = np.hypot(a1, b1); c2 = np.hypot(a2, b2); cm = (c1 + c2) / 2
    g = .5 * (1 - np.sqrt(cm**7 / (cm**7 + 25**7)))
    ap1 = (1 + g) * a1; ap2 = (1 + g) * a2
    cp1 = np.hypot(ap1, b1); cp2 = np.hypot(ap2, b2)
    hp1 = np.degrees(np.arctan2(b1, ap1)) % 360
    hp2 = np.degrees(np.arctan2(b2, ap2)) % 360
    dl = l2 - l1; dc = cp2 - cp1; dh = hp2 - hp1
    both_chromatic = (cp1 * cp2) != 0
    dh = np.where(~both_chromatic, 0, np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh)))
    d_h = 2 * np.sqrt(cp1 * cp2) * np.sin(np.radians(dh / 2))
    lm = (l1 + l2) / 2; cpm = (cp1 + cp2) / 2
    hm = (hp1 + hp2) / 2
    hm = np.where(~both_chromatic, hp1 + hp2,
        np.where(np.abs(hp1 - hp2) <= 180, hm,
            np.where(hp1 + hp2 < 360, hm + 180, hm - 180)))
    t = (1 - .17 * np.cos(np.radians(hm - 30)) + .24 * np.cos(np.radians(2 * hm))
         + .32 * np.cos(np.radians(3 * hm + 6)) - .20 * np.cos(np.radians(4 * hm - 63)))
    sl = 1 + .015 * (lm - 50)**2 / np.sqrt(20 + (lm - 50)**2)
    sc = 1 + .045 * cpm; sh = 1 + .015 * cpm * t
    rt = -2 * np.sqrt(cpm**7 / (cpm**7 + 25**7)) * np.sin(np.radians(60 * np.exp(-((hm - 275) / 25)**2)))
    return np.sqrt((dl / sl)**2 + (dc / sc)**2 + (d_h / sh)**2 + rt * (dc / sc) * (d_h / sh))


def review(manifest: dict[str, Any], root: Path, project_rules: list[ProjectRule] | None = None) -> dict[str, Any]:
    if manifest.get("intent",{}).get("kind")=="game_trace":
        from .game import load_trace, analyze_trace
        asset=asset_path(manifest,root); trace=load_trace(asset); game=analyze_trace(trace,manifest.get("game_policy",{}),asset.parent)
        adapter_id=f"{trace.engine}_trace" if trace.engine in {"godot","unity"} else "game_trace"
        game_source=ADAPTERS[adapter_id].extract(asset,set())
        expected=manifest.get("asset",{}).get("sha256")
        image_check=_rule_result("IMAGE-001",None,"image_metadata",game_source,{"sha256":expected},
            "PASS" if expected in (None,game_source["sha256"]) else "FAIL","SHA256(file bytes) equals asset.sha256 when provided")
        game_results=[image_check]
        if manifest.get("schema_version") == "0.6":
            inventory=manifest.get("feature_inventory",[])
            unresolved=[item["id"] for item in inventory if item["status"] not in {"approved","not_applicable"} or any(candidate["status"]=="unreviewed" for candidate in item.get("candidates",[]))]
            game_results.append(_rule_result("FEATURE-COVERAGE-001",None,"feature_inventory_completeness",
                {"required":len(inventory),"approved":sum(item["status"]=="approved" for item in inventory),
                 "not_applicable":sum(item["status"]=="not_applicable" for item in inventory),"unresolved":unresolved,
                 "items":[{"id":item["id"],"status":item["status"],"feature_id":item.get("feature_id"),"candidates":item.get("candidates",[])} for item in inventory]},
                {"all_required_features":"approved or explicitly not_applicable"},"PASS" if not unresolved else "INCONCLUSIVE",
                "every requested feature has an approved feature mapping or a reasoned not_applicable disposition"))
        for row in game["results"]:
            game_results.append({"rule_id":f"GAME-{row.get('algorithm','trace').upper().replace('_','-')}","feature_id":row.get("id"),
                            "algorithm":row.get("algorithm","game_trace_validation"),"algorithm_version":row.get("version","1.0"),
                            "status":row["status"],"measured":row.get("measured"),"threshold":row.get("threshold"),
                            "formula":row.get("formula",""),**({"reason":row["reason"]} if row.get("reason") else {})})
        planned=resolve_rules(manifest)
        present={row.get("algorithm") for row in game_results}
        for check in planned:
            if check["algorithm"] not in present:
                game_results.append(_rule_result(check["rule_id"],None,check["algorithm"],None,manifest.get("game_policy",{}),"INCONCLUSIVE",
                    "trace contains no evidence for this planned check"))
        if len(game_results)==1:
            game_results.append(_rule_result("GAME-TRACE-001",None,"game_trace_validation",None,None,"INCONCLUSIVE","trace contains no measurable tracks, events, contacts, or particles"))
        covered=[{"rule_id":r["rule_id"],"feature_id":r.get("feature_id"),"status":r["status"]} for r in game_results]
        complete=all(r["status"] in {"PASS","FAIL"} for r in game_results)
        overall="FAIL" if any(r["status"]=="FAIL" for r in game_results) else ("PASS" if complete else "INCONCLUSIVE")
        return {"schema_version":"0.5","asset_type":"game_trace","scenario_id":manifest.get("scenario_id"),"status":overall,"completeness":"COMPLETE" if complete else "INCOMPLETE",
                "coverage":{"required":len(game_results),"completed":sum(r["status"] in {"PASS","FAIL"} for r in game_results),
                            "missing":[r for r in covered if r["status"] not in {"PASS","FAIL"}],"checks":covered},
                "asset":game_source,"asset_id":manifest.get("asset",{}).get("id"),"rule_registry_version":"1",
                "results":game_results}
    source_manifest=manifest
    matched=match_project_rules(manifest,project_rules or [])
    if matched: manifest,policy_applications=apply_project_rules(manifest,matched)
    else: policy_applications=[]
    execution_plan=plan_review(source_manifest,project_rules)
    asset = asset_path(manifest, root)
    inspection = inspect_image(asset)
    expected = manifest["asset"].get("sha256")
    results: list[dict[str, Any]] = []
    results.append(_rule_result("IMAGE-001", None, "image_metadata", inspection,
                                {"sha256": expected}, "PASS" if expected in (None, inspection["sha256"]) else "FAIL",
                                "SHA256(file bytes) equals asset.sha256 when provided"))
    if inspection["format"] == "SVG":
        svg_root = ET.parse(asset).getroot()
        stroke_widths = []
        for node in svg_root.iter():
            raw = node.attrib.get("stroke-width")
            if raw:
                try: stroke_widths.append(float(raw.removesuffix("px")))
                except ValueError: pass
        constraints = manifest.get("constraints", {})
        limit = constraints.get("max_svg_gradients")
        if limit is not None:
            count = sum(inspection["gradient_declarations"].values())
            results.append(_rule_result("SVG-001", None, "svg_gradient_declarations", count, limit,
                "PASS" if count <= limit else "FAIL", "count(linearGradient, radialGradient) <= max_svg_gradients"))
        max_stroke = constraints.get("max_svg_stroke_width")
        if max_stroke is not None:
            stroke_measurement = max(stroke_widths) if stroke_widths else None
            status = "INCONCLUSIVE" if stroke_measurement is None else ("PASS" if stroke_measurement <= max_stroke else "FAIL")
            results.append(_rule_result("SVG-002", None, "svg_stroke_width_range", stroke_measurement, max_stroke, status,
                "max(explicit numeric stroke-width values); CSS and unit conversion are not inferred"))
        for f in manifest.get("features", []):
            props = f.get("properties", {})
            if props.get("gradient_allowed") is False:
                n = sum(inspection["gradient_declarations"].values())
                results.append(_rule_result("SVG-001", f["id"], "svg_gradient_declarations", n, 0,
                    "PASS" if n == 0 else "FAIL", "count(linearGradient, radialGradient) == 0"))
            if props.get("max_svg_stroke_width") is not None:
                stroke_measurement = max(stroke_widths) if stroke_widths else None
                threshold = props["max_svg_stroke_width"]
                status = "INCONCLUSIVE" if stroke_measurement is None else ("PASS" if stroke_measurement <= threshold else "FAIL")
                results.append(_rule_result("SVG-002", f["id"], "svg_stroke_width_range", stroke_measurement, threshold, status,
                    "maximum explicit numeric stroke-width; stylesheet and inherited values excluded"))
            if any(key in props for key in ("max_path_length","min_path_length","min_path_segments","max_path_aspect_ratio")):
                target=props.get("svg_path_id")
                candidates=[item for item in inspection["paths"] if item["id"] == target] if target is not None else inspection["paths"]
                if len(candidates)!=1:
                    results.append(_rule_result("SVG-003", f["id"], "svg_path_geometry", None, {k:props[k] for k in props if k.startswith(("min_path_","max_path_"))}, "INCONCLUSIVE", "set svg_path_id when the SVG has multiple paths or no unique target"))
                    continue
                path_info=candidates[0]
                if path_info["transform"]:
                    results.append(_rule_result("SVG-003", f["id"], "svg_path_geometry", path_info, None, "INCONCLUSIVE", "path or ancestor transform is present; transformed coordinates are not applied"))
                    continue
                checks=[]
                undefined=False
                for key,metric,op in (("max_path_length","length","le"),("min_path_length","length","ge"),
                                      ("min_path_segments","segment_count","ge"),("max_path_aspect_ratio","aspect_ratio","le")):
                    if key not in props: continue
                    value=(path_info["width"]/path_info["height"] if path_info["height"] else None) if metric=="aspect_ratio" else path_info[metric]
                    if value is None: undefined=True; passed=False
                    else: passed=value<=props[key] if op=="le" else value>=props[key]
                    checks.append((key,value,props[key],passed))
                state="INCONCLUSIVE" if undefined else ("PASS" if all(item[3] for item in checks) else "FAIL")
                results.append(_rule_result("SVG-003", f["id"], "svg_path_geometry", {"path":path_info,"checks":[{"constraint":k,"measured":v,"threshold":t,"pass":ok} for k,v,t,ok in checks]},
                    {k:props[k] for k,_,_,_ in checks}, state, "svgpathtools path bbox, analytic segment geometry, and curve-length integration"))
    else:
        with Image.open(asset) as image_source:
            alpha_present = "A" in image_source.getbands() or "transparency" in image_source.info
            image = image_source.convert("RGBA")
            rgba_array = np.asarray(image, dtype=np.uint8)
            alpha_array = rgba_array[:, :, 3]
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            if bbox and alpha_present:
                weights = alpha_array.astype(np.float64, copy=False)
                total = float(weights.sum())
                x_weights = weights.sum(axis=0)
                y_weights = weights.sum(axis=1)
                cx = float(np.dot(np.arange(image.width) + .5, x_weights) / total)
                cy = float(np.dot(np.arange(image.height) + .5, y_weights) / total)
                offset = math.hypot((cx-image.width/2)/image.width, (cy-image.height/2)/image.height)
            else:
                offset = None
            max_offset = manifest.get("constraints", {}).get("max_normalized_centroid_offset")
            if max_offset is not None:
                status = "INCONCLUSIVE" if offset is None else ("PASS" if offset <= max_offset else "FAIL")
                results.append(_rule_result("GEOMETRY-001", None, "alpha_centroid_offset", offset, max_offset,
                    status, "hypot((cx-W/2)/W, (cy-H/2)/H), alpha-weighted centroid", list(bbox) if bbox else None))
            constraints = manifest.get("constraints", {})
            max_palette = constraints.get("max_palette_colors")
            if max_palette is not None:
                count = int(np.unique(rgba_array[:, :, :3][alpha_array > 0], axis=0).shape[0])
                status = "PASS" if count <= max_palette else "FAIL"
                results.append(_rule_result("COLOR-001", None, "exact_palette_count", count, max_palette, status,
                    "number of distinct visible RGBA tuples; transparent pixels excluded"))
            margins = constraints.get("min_margin_ratio")
            if margins is not None:
                if bbox is None:
                    margin_measurement, status = None, "INCONCLUSIVE"
                else:
                    margin_measurement = {"left": bbox[0]/image.width, "top": bbox[1]/image.height,
                                "right": (image.width-bbox[2])/image.width, "bottom": (image.height-bbox[3])/image.height}
                    status = "PASS" if min(margin_measurement.values()) >= margins else "FAIL"
                results.append(_rule_result("GEOMETRY-002", None, "alpha_margin_ratio", margin_measurement, margins,
                    status, "minimum of four transparent alpha-bbox margins divided by canvas dimension", list(bbox) if bbox else None))
            mask_cache: dict[Path, Image.Image] = {}
            for f in manifest.get("features", []):
                props = f.get("properties", {})
                if props.get("gradient_allowed") is False:
                    results.append(_rule_result("GRADIENT-001", f["id"], "raster_gradient_semantic_check", None, "no gradient intended", "INCONCLUSIVE",
                        "raster color variation can come from shading or texture; semantic gradient presence cannot be proven from pixels"))
                geom = f.get("geometry", {})
                wants_mask_rule = any(k in props for k in ("max_channel_range", "max_delta_e00", "max_exceedance_ratio", "max_palette_colors", "max_connected_components", "min_symmetry_iou"))
                wants_mask_rule |= any(k in props for k in ("max_periodic_energy_ratio", "max_luma_levels", "max_silhouette_components", "min_occupancy_ratio", "max_gradient_fit_r2", "seamless_tile", "max_holes", "min_width_px", "max_boundary_distance_px", "min_iou", "min_dice", "max_skeleton_endpoints", "max_skeleton_branches", "max_hu_shape_distance", "min_tenengrad", "min_boundary_gradient"))
                mask = None
                mask_array: np.ndarray = np.zeros((image.height, image.width), dtype=np.bool_)
                if wants_mask_rule and geom.get("type") == "mask":
                    mask_path = _safe_path(root, geom["path"])
                    try:
                        if mask_path not in mask_cache:
                            mask_cache[mask_path] = FEATURE_ADAPTERS["mask"].extract(mask_path,image.size)
                        mask=mask_cache[mask_path]
                        mask_array=np.asarray(mask) > 0
                    except (OSError,ValueError) as exc: raise ManifestError(f"feature {f['id']}: {exc}") from exc
                if props.get("surface") == "solid" and props.get("max_channel_range") is not None:
                    geom = f.get("geometry", {})
                    if mask is None:
                        results.append(_rule_result("SOLID-001", f["id"], "masked_color_uniformity", None,
                            props["max_channel_range"], "INCONCLUSIVE", "requires a feature mask"))
                    else:
                        pixels = rgba_array[:, :, :3][mask_array & (alpha_array > 0)]
                        if pixels.size == 0:
                            value, status = None, "INCONCLUSIVE"
                        else:
                            value = int(np.ptp(pixels, axis=0).max())
                            status = "PASS" if value <= props["max_channel_range"] else "FAIL"
                        results.append(_rule_result("SOLID-001", f["id"], "masked_color_uniformity", value,
                            props["max_channel_range"], status, "max_c(max(RGB_c)-min(RGB_c)) over mask", _bbox_list(mask)))
                if props.get("surface")=="solid" and ("max_delta_e00" in props or "max_exceedance_ratio" in props):
                    if mask is None:
                        delta_measurement,state=None,"INCONCLUSIVE"
                    else:
                        samples=rgba_array[:, :, :3][mask_array & (alpha_array > 0)]
                        if samples.size == 0:
                            delta_measurement,state=None,"INCONCLUSIVE"
                        else:
                            if "reference_rgb" in props:
                                reference=np.asarray(props["reference_rgb"],dtype=np.uint8)
                            else:
                                middle=samples.shape[0]//2
                                reference=np.partition(samples,middle,axis=0)[middle]
                            ref_lab=_rgb_array_lab(reference.reshape(1,3))[0]
                            distances=_delta_e_2000_array(_rgb_array_lab(samples),ref_lab)
                            tolerance=props.get("delta_e_tolerance",2.0)
                            exceed=float(np.count_nonzero(distances>tolerance)/distances.size)
                            delta_measurement={"max_delta_e00":float(distances.max()),"exceedance_ratio":exceed,"reference_rgb":reference.tolist()}
                            max_ok="max_delta_e00" not in props or delta_measurement["max_delta_e00"]<=props["max_delta_e00"]
                            ratio_ok="max_exceedance_ratio" not in props or exceed<=props["max_exceedance_ratio"]
                            state="PASS" if max_ok and ratio_ok else "FAIL"
                    results.append(_rule_result("SOLID-002",f["id"],"ciede2000_uniformity",delta_measurement,
                        {k:props[k] for k in ("max_delta_e00","max_exceedance_ratio","delta_e_tolerance") if k in props} or {"delta_e_tolerance":2.0},state,
                        "CIEDE2000 in D65 Lab versus reference_rgb or per-channel median; exceedance uses delta_e_tolerance",_bbox_list(mask)))
                for prop, rule_id, algorithm, label in (("max_palette_colors","COLOR-002","masked_palette_count","unique visible RGB tuples inside mask"),
                    ("max_connected_components","TOPOLOGY-001","mask_connected_components","8-connected foreground component count")):
                    if prop not in props: continue
                    if mask is None:
                        results.append(_rule_result(rule_id, f["id"], algorithm, None, props[prop], "INCONCLUSIVE", "requires a feature mask")); continue
                    if prop == "max_palette_colors":
                        if "palette_bin_size_lab" in props:
                            bins=props["palette_bin_size_lab"]
                            if not isinstance(bins,list) or len(bins)!=3 or any(not isinstance(v,(int,float)) or v<=0 for v in bins):
                                raise ManifestError(f"feature {f['id']}: palette_bin_size_lab must contain three positive values")
                            rgb=rgba_array[:, :, :3][mask_array & (alpha_array > 0)]
                            lab=_rgb_array_lab(rgb)
                            count=int(np.unique(np.floor(lab/np.asarray(bins)),axis=0).shape[0])
                        else:
                            rgb=rgba_array[:, :, :3][mask_array & (alpha_array > 0)]
                            count=int(np.unique(rgb,axis=0).shape[0])
                        feature_measurement = count
                    else:
                        feature_measurement, _, _ = _mask_metrics(mask)
                    results.append(_rule_result(rule_id, f["id"], algorithm, feature_measurement, props[prop], "PASS" if feature_measurement <= props[prop] else "FAIL", label, _bbox_list(mask)))
                if "min_symmetry_iou" in props:
                    if mask is None:
                        results.append(_rule_result("SYMMETRY-001", f["id"], "mask_reflection_iou", None, props["min_symmetry_iou"], "INCONCLUSIVE", "requires a feature mask"))
                    else:
                        reflected = np.fliplr(mask_array)
                        intersection = int(np.count_nonzero(mask_array & reflected))
                        union = int(np.count_nonzero(mask_array | reflected))
                        iou = intersection / union if union else 1.0
                        results.append(_rule_result("SYMMETRY-001", f["id"], "mask_reflection_iou", iou, props["min_symmetry_iou"], "PASS" if iou >= props["min_symmetry_iou"] else "FAIL", "intersection(mask, horizontal_reflection)/union", _bbox_list(mask)))
                if props.get("seamless_tile") is True:
                    # Compare all visible RGBA channels so alpha-only seams are detected.
                    arr = np.asarray(image.convert("RGBA"), dtype=np.int16)
                    vertical = np.abs(arr[:,0,:] - arr[:,-1,:]).mean() if image.width else 0
                    horizontal = np.abs(arr[0,:,:] - arr[-1,:,:]).mean() if image.height else 0
                    tile_measurement = float(max(vertical, horizontal)); limit = props.get("max_tile_edge_error", 0.0)
                    results.append(_rule_result("BACKGROUND-001", f["id"], "tile_edge_color_error", tile_measurement, limit,
                        "PASS" if tile_measurement <= limit else "FAIL", "max(mean(abs(left-right)), mean(abs(top-bottom))) RGBA channel levels"))
                if "min_occupancy_ratio" in props:
                    if mask is None: ratio, state = None, "INCONCLUSIVE"
                    else:
                        ratio = float(np.count_nonzero(mask_array) / (image.width * image.height))
                        state = "PASS" if ratio >= props["min_occupancy_ratio"] else "FAIL"
                    results.append(_rule_result("BACKGROUND-003", f["id"], "region_occupancy", ratio, props["min_occupancy_ratio"], state, "foreground mask pixels / canvas pixels", _bbox_list(mask)))
                if "max_luma_levels" in props:
                    if mask is None: levels, state = None, "INCONCLUSIVE"
                    else:
                        pixels = rgba_array[:, :, :3][mask_array & (alpha_array > 0)]
                        luma = _linear_srgb_luma(pixels)
                        levels = int(np.unique(np.floor(luma / props.get("luma_bin_width", 32))).size)
                        state = "PASS" if levels <= props["max_luma_levels"] else "FAIL"
                    results.append(_rule_result("CHARACTER-001", f["id"], "masked_luma_levels", levels, props["max_luma_levels"], state, f"unique luma bins of width {props.get('luma_bin_width',32)}"))
                if "max_silhouette_components" in props:
                    if mask is None: comp, state = None, "INCONCLUSIVE"
                    else:
                        comp, _, _ = _mask_metrics(mask); state = "PASS" if comp <= props["max_silhouette_components"] else "FAIL"
                    results.append(_rule_result("CHARACTER-002", f["id"], "silhouette_metrics", {"components":comp,"bbox":_bbox_list(mask)}, props["max_silhouette_components"], state, "8-connected components and alpha-mask bounding box"))
                visible_mask = mask_array & (alpha_array > 0)
                if "max_holes" in props:
                    holes_count = topology(visible_mask)["holes"] if mask is not None and np.any(visible_mask) else None
                    state = "INCONCLUSIVE" if holes_count is None else ("PASS" if holes_count <= props["max_holes"] else "FAIL")
                    results.append(_rule_result("TOPOLOGY-002", f["id"], "mask_hole_count", holes_count, props["max_holes"], state,
                        "enclosed 4-connected background components inside the visible feature mask", _bbox_list(mask)))
                if any(key in props for key in ("max_skeleton_endpoints", "max_skeleton_branches")):
                    graph_metrics = skeleton_graph(visible_mask) if mask is not None else None
                    for prop, rule_id, algorithm, metric in (
                        ("max_skeleton_endpoints", "TOPOLOGY-003", "mask_skeleton_endpoints", "endpoints"),
                        ("max_skeleton_branches", "TOPOLOGY-004", "mask_skeleton_branches", "branch_points"),
                    ):
                        if prop not in props: continue
                        measured = None if graph_metrics is None else graph_metrics[metric]
                        state = "INCONCLUSIVE" if measured is None else ("PASS" if measured <= props[prop] else "FAIL")
                        results.append(_rule_result(rule_id, f["id"], algorithm, measured, props[prop], state,
                            f"{metric} in deterministic morphological skeleton graph", _bbox_list(mask)))
                if "min_width_px" in props:
                    width_value = minimum_width(visible_mask) if mask is not None and np.any(visible_mask) else None
                    state = "INCONCLUSIVE" if width_value is None else ("PASS" if width_value >= props["min_width_px"] else "FAIL")
                    results.append(_rule_result("GEOMETRY-003", f["id"], "mask_minimum_width", width_value, props["min_width_px"], state,
                        "minimum twice-distance-transform radius at local ridge maxima, in pixels", _bbox_list(mask)))
                if any(key in props for key in ("max_boundary_distance_px", "min_iou", "min_dice")):
                    comparison: dict[str, float | None] = {"boundary_distance_px": None, "iou": None, "dice": None}
                    if mask is not None and np.any(visible_mask) and props.get("reference_mask"):
                        reference_path = _safe_path(root, props["reference_mask"])
                        try:
                            with Image.open(reference_path) as reference_image:
                                reference_mask = np.asarray(reference_image.convert("L")) > 0
                            comparison = compare_masks(visible_mask, reference_mask)
                        except (OSError, ValueError) as exc:
                            raise ManifestError(f"feature {f['id']}: cannot compare reference mask: {exc}") from exc
                    for prop, rule_id, algorithm, metric, op in (
                        ("max_boundary_distance_px", "GEOMETRY-004", "mask_boundary_distance", "boundary_distance_px", "le"),
                        ("min_iou", "GEOMETRY-005", "mask_iou_dice", "iou", "ge"),
                        ("min_dice", "GEOMETRY-006", "mask_dice", "dice", "ge"),
                    ):
                        if prop not in props: continue
                        comparison_value = comparison[metric]
                        state = "INCONCLUSIVE" if comparison_value is None else ("PASS" if (comparison_value <= props[prop] if op == "le" else comparison_value >= props[prop]) else "FAIL")
                        results.append(_rule_result(rule_id, f["id"], algorithm, comparison_value, props[prop], state,
                            f"{metric} against aligned reference_mask in pixel coordinates", _bbox_list(mask)))
                if "max_hu_shape_distance" in props:
                    shape_distance: float | None = None
                    if mask is not None and props.get("reference_mask"):
                        reference_path = _safe_path(root, props["reference_mask"])
                        with Image.open(reference_path) as reference_image:
                            reference_mask = np.asarray(reference_image.convert("L")) > 0
                        shape_distance = hu_shape_distance(visible_mask, reference_mask)
                    state = "INCONCLUSIVE" if shape_distance is None else ("PASS" if shape_distance <= props["max_hu_shape_distance"] else "FAIL")
                    results.append(_rule_result("GEOMETRY-007", f["id"], "mask_hu_shape_distance", shape_distance,
                        props["max_hu_shape_distance"], state, "Euclidean distance between signed-log Hu moment vectors", _bbox_list(mask)))
                if "min_tenengrad" in props or "min_boundary_gradient" in props:
                    edge_metrics = roi_edge_metrics(rgba_array[:, :, :3], visible_mask) if mask is not None else {"tenengrad": None, "boundary_gradient": None}
                    for prop, rule_id, algorithm, metric in (
                        ("min_tenengrad", "RASTER-EDGE-001", "roi_tenengrad_sharpness", "tenengrad"),
                        ("min_boundary_gradient", "RASTER-EDGE-002", "mask_boundary_gradient", "boundary_gradient"),
                    ):
                        if prop not in props: continue
                        edge_value = edge_metrics[metric]
                        state = "INCONCLUSIVE" if edge_value is None else ("PASS" if edge_value >= props[prop] else "FAIL")
                        results.append(_rule_result(rule_id, f["id"], algorithm, edge_value, props[prop], state,
                            "Sobel gradient over ROI pixels; intensity input uses RGB-derived luma on 0..255 scale", _bbox_list(mask)))
                if mask is not None and not np.any(mask_array & (alpha_array > 0)):
                    for finding in results:
                        if finding.get("feature_id") == f["id"] and finding.get("algorithm") in {
                            "masked_palette_count", "mask_connected_components", "mask_reflection_iou",
                            "masked_luma_levels", "silhouette_metrics", "region_occupancy",
                            "ciede2000_uniformity", "masked_color_uniformity", "fft_periodic_energy_ratio",
                        }:
                            finding["status"] = "INCONCLUSIVE"
                            finding["reason"] = "feature mask contains no visible asset pixels"
                if "max_periodic_energy_ratio" in props:
                    if mask is None: ratio, state = None, "INCONCLUSIVE"
                    else:
                        gray = np.asarray(image.convert("L"), dtype=np.float64)
                        yy, xx = np.nonzero(np.asarray(mask) > 0)
                        if len(xx) < 4: ratio, state = None, "INCONCLUSIVE"
                        else:
                            x1,x2,y1,y2=xx.min(),xx.max()+1,yy.min(),yy.max()+1
                            crop=gray[y1:y2,x1:x2]; spectrum=np.abs(np.fft.fft2(crop))**2
                            spectrum[0,0]=0; total=float(spectrum.sum())
                            peaks=np.partition(spectrum.ravel(), max(0,spectrum.size-9))[-8:]
                            ratio=float(peaks.sum()/total) if total else 0.0
                            state="PASS" if ratio <= props["max_periodic_energy_ratio"] else "FAIL"
                    results.append(_rule_result("BACKGROUND-002", f["id"], "fft_periodic_energy_ratio", ratio, props["max_periodic_energy_ratio"], state, "sum(top 8 2D FFT power bins except DC) / total power; bbox crop of mask"))
                if "max_gradient_fit_r2" in props:
                    if mask is None: fit_r2, state = None, "INCONCLUSIVE"
                    else:
                        ys, xs = np.nonzero(mask_array & (alpha_array > 0))
                        observed = rgba_array[ys, xs, :3].astype(float)
                        if len(xs)<4: fit_r2,state=None,"INCONCLUSIVE"
                        else:
                            design=np.column_stack((xs, ys, np.ones(len(xs))))
                            predicted=design@np.linalg.lstsq(design,observed,rcond=None)[0]
                            denominator=float(((observed-observed.mean(axis=0))**2).sum())
                            fit_r2=float(1-((observed-predicted)**2).sum()/denominator) if denominator else 1.0
                            state="INCONCLUSIVE" # High plane fit may be lighting, shading, or a true gradient.
                    results.append(_rule_result("GRADIENT-002", f["id"], "linear_gradient_fit_diagnostic", fit_r2, props["max_gradient_fit_r2"], state, "multi-channel least-squares RGB plane R-squared; diagnostic only, never a semantic verdict"))
            feature_by_id={f["id"]:f for f in manifest.get("features",[])}
            for constraint in manifest.get("overlap_constraints",[]):
                fa,fb=feature_by_id[constraint["feature_a"]],feature_by_id[constraint["feature_b"]]
                ga,gb=fa.get("geometry",{}),fb.get("geometry",{})
                if ga.get("type")!="mask" or gb.get("type")!="mask":
                    ratio,state=None,"INCONCLUSIVE"
                else:
                    paths: list[Path] = [_safe_path(root, ga["path"]), _safe_path(root, gb["path"])]
                    arrays: list[np.ndarray] = []
                    for mask_path in paths:
                        if mask_path not in mask_cache:
                            mask_cache[mask_path] = FEATURE_ADAPTERS["mask"].extract(mask_path, image.size)
                        if mask_cache[mask_path].size != image.size: raise ManifestError("overlap masks must match asset dimensions")
                        arrays.append(np.asarray(mask_cache[mask_path]) > 0)
                    aa, bb = arrays
                    area_a=int(np.count_nonzero(aa)); area_b=int(np.count_nonzero(bb)); overlap=int(np.count_nonzero(aa & bb))
                    ratio=overlap/min(area_a,area_b) if min(area_a,area_b) else None
                    state="INCONCLUSIVE" if ratio is None else ("PASS" if ratio<=constraint["max_overlap_ratio"] else "FAIL")
                results.append(_rule_result("OVERLAP-001", f"{fa['id']}:{fb['id']}", "mask_intersection_ratio", ratio, constraint["max_overlap_ratio"], state,
                    "intersection area / smaller feature area"))
    for application in policy_applications:
        rule=application["rule"]
        row=next((r for r in results if r.get("rule_id")==application["rule_id"] and r.get("feature_id")==application["feature_id"] and r.get("algorithm")==application["algorithm_id"] and (rule.algorithm.id!="mask_intersection_ratio" or r.get("threshold")==application["threshold"])),None)
        if rule.algorithm.id == "mask_intersection_ratio" and row is not None:
            row=dict(row)
            synthetic=next((r for r in results if r.get("rule_id")==application["rule_id"] and r.get("feature_id")==application["feature_id"] and r.get("algorithm")==application["algorithm_id"] and r.get("threshold")==application["threshold"]),None)
            if synthetic is not None: results.remove(synthetic)
            results.append(row)
            row["rule_id"]=rule.id
            row["threshold"]=application["threshold"]
            row["status"]="INCONCLUSIVE" if row.get("measured") is None else ("PASS" if row["measured"] <= application["threshold"] else "FAIL")
            row["constraint_origin"]=rule.assertion.origin
            continue
        if row is None:
            state="SKIPPED" if rule.on_missing=="skip" else "INCONCLUSIVE"
            item=_rule_result(rule.id,application["feature_id"],rule.algorithm.id,None,application["threshold"],state,
                "required measurement was not produced")
            item["reason"]="missing required measurement"; item["algorithm_version"]=rule.algorithm.version
            item["constraint_origin"]=rule.assertion.origin; results.append(item)
            continue
        if row is not None:
            row["rule_id"]=rule.id; row["algorithm_version"]=rule.algorithm.version
            row["constraint_origin"]=rule.assertion.origin; row["threshold"]=application["threshold"]; row["algorithm"]=rule.algorithm.id
            if row["status"]=="INCONCLUSIVE" and rule.on_missing=="skip":
                row["status"]="SKIPPED"; row["reason"]="rule configured to skip when required evidence is missing"

    planned=resolve_rules(source_manifest,project_rules)
    if source_manifest.get("schema_version") == "0.6":
        planned.append({"rule_id":"FEATURE-COVERAGE-001","feature_id":None,"algorithm":"feature_inventory_completeness"})
    plan_checks={(item["rule_id"],item.get("feature_id")):item for item in execution_plan["checks"]}
    for check in planned:
        if check["algorithm"] == "feature_inventory_completeness":
            inventory=source_manifest.get("feature_inventory",[])
            unresolved=[item["id"] for item in inventory if item["status"] not in {"approved","not_applicable"} or any(candidate["status"]=="unreviewed" for candidate in item.get("candidates",[]))]
            status="PASS" if not unresolved else "INCONCLUSIVE"
            results.append(_rule_result(check["rule_id"],None,check["algorithm"],
                {"required":len(inventory),"approved":sum(item["status"]=="approved" for item in inventory),
                 "not_applicable":sum(item["status"]=="not_applicable" for item in inventory),"unresolved":unresolved,
                 "items":[{"id":item["id"],"status":item["status"],"feature_id":item.get("feature_id"),"candidates":item.get("candidates",[])} for item in inventory]},
                {"all_required_features":"approved or explicitly not_applicable"},status,
                "every requested feature has an approved feature mapping or a reasoned not_applicable disposition"))
            continue
        plan_check=plan_checks.get((check["rule_id"],check.get("feature_id")))
        if plan_check is None or plan_check["status"]=="READY":
            continue
        row=next((r for r in results if r.get("rule_id")==check["rule_id"] and r.get("feature_id")==check.get("feature_id")),None)
        status="SKIPPED" if check.get("on_missing")=="skip" else "INCONCLUSIVE"
        if plan_check["status"]=="MISSING_CAPABILITY":
            reason="required capabilities are unavailable: " + ", ".join(plan_check["required_capabilities"])
        elif plan_check["status"]=="UNSUPPORTED_VERSION":
            reason=f"requested algorithm version {plan_check.get('requested_algorithm_version')} is not available"
        else:
            reason="required measurement data is unavailable"
        if row is None:
            results.append(_rule_result(check["rule_id"],check.get("feature_id"),check["algorithm"],None,
                check, status, reason))
        else:
            row["status"]=status
            row["reason"]=reason
    for finding in results:
        algorithm=ALGORITHM_REGISTRY.get(finding.get("algorithm"),{})
        finding["algorithm_version"]=algorithm.get("version","1.0")
        feature=next((f for f in source_manifest.get("features",[]) if f["id"]==finding.get("feature_id")),None)
        provenance=(feature or {}).get("provenance",{})
        if not provenance: provenance=(feature or {}).get("evidence",{})
        finding.setdefault("constraint_origin", provenance.get("origin") or source_manifest.get("intent",{}).get("origin") or "unspecified")
    covered=[]; missing=[]
    for check in planned:
        found=next((r for r in results if r.get("rule_id")==check["rule_id"] and r.get("feature_id")==check.get("feature_id")),None)
        if found is None:
            missing.append({**check,"status":"SKIPPED","reason":"planned rule did not produce a result"})
        else: covered.append({"rule_id":check["rule_id"],"feature_id":check.get("feature_id"),"status":found["status"]})
    complete=not missing and all(x["status"] in {"PASS","FAIL"} for x in covered)
    statuses=[r["status"] for r in results]
    overall="FAIL" if "FAIL" in statuses else ("PASS" if complete else "INCONCLUSIVE")
    feature_masks = {}
    for item in source_manifest.get("features", []):
        geometry = item.get("geometry", {})
        if geometry.get("type") == "mask":
            mask_path = _safe_path(root, geometry["path"])
            with Image.open(mask_path) as opened:
                feature_masks[item["id"]] = {"path":geometry["path"], "sha256":hashlib.sha256(mask_path.read_bytes()).hexdigest(),
                                              "width":opened.width, "height":opened.height}
    return {"schema_version": "0.5", "status": overall, "completeness":"COMPLETE" if complete else "INCOMPLETE",
            "coverage":{"required":len(planned),"completed":sum(x["status"] in {"PASS","FAIL"} for x in covered),"missing":missing,"checks":covered},
            "asset": inspection, "asset_id":manifest.get("asset",{}).get("id"), "rule_registry_version":"1",
            "allowed_region":manifest.get("regression",{}).get("allowed_region"),
            "allowed_mask":str(_safe_path(root,manifest["regression"]["allowed_mask"])) if manifest.get("regression",{}).get("allowed_mask") else None,
            "feature_masks":feature_masks, "results": results}


def verify_images(before: Path, after: Path, allowed_region: tuple[int, int, int, int] | None = None, allowed_mask: Path | None = None) -> dict[str, Any]:
    """Compare raster images and report changes outside an optional editable rectangle."""
    try:
        with Image.open(before) as before_file, Image.open(after) as after_file:
            image_a = before_file.convert("RGBA")
            image_b = after_file.convert("RGBA")
            before_array = np.asarray(image_a, dtype=np.uint8)
            after_array = np.asarray(image_b, dtype=np.uint8)
    except Exception as exc:
        raise ManifestError(f"cannot decode comparison images: {exc}") from exc
    if image_a.size != image_b.size:
        return {"status": "FAIL", "reason": "dimension_mismatch", "before_size": image_a.size, "after_size": image_b.size}

    changed = np.any(before_array != after_array, axis=2)
    allowed = np.zeros(changed.shape, dtype=np.bool_)
    if allowed_region is not None:
        x1, y1, x2, y2 = allowed_region
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image_a.width, x2), min(image_a.height, y2)
        if x1 < x2 and y1 < y2:
            allowed[y1:y2, x1:x2] = True

    if allowed_mask is not None:
        try:
            with Image.open(allowed_mask) as opened:
                allowed_image = opened.convert("L")
                if allowed_image.size != image_a.size:
                    raise ManifestError("allowed mask dimensions must match the images")
                allowed |= np.asarray(allowed_image) > 0
        except ManifestError:
            raise
        except Exception as exc:
            raise ManifestError(f"cannot decode allowed mask: {exc}") from exc

    changed_count = int(np.count_nonzero(changed))
    outside_count = int(np.count_nonzero(changed & ~allowed))
    ys, xs = np.nonzero(changed)
    changed_bbox = None if not len(xs) else [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return {"status": "PASS" if outside_count == 0 else "FAIL", "changed_pixels": changed_count,
            "changed_ratio": changed_count/(image_a.width*image_a.height), "changed_bbox": changed_bbox,
            "allowed_region": allowed_region, "allowed_mask":str(allowed_mask) if allowed_mask else None,
            "changed_pixels_outside_allowed_region": outside_count,
            "formula": "count(before RGBA != after RGBA); comparison is exact per pixel"}
