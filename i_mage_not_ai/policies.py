"""Project YAML rule loading, deterministic matching, and priority conflict checks."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
from copy import deepcopy
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SUPPORTED_ALGORITHMS={"exact_palette_count","quantized_palette_count","alpha_centroid_offset","alpha_margin_ratio","masked_color_uniformity","ciede2000_uniformity","mask_connected_components","mask_hole_count","mask_minimum_width","mask_boundary_distance","mask_iou_dice","mask_dice","mask_reflection_iou","mask_intersection_ratio","tile_edge_color_error","fft_periodic_energy_ratio","svg_gradient_declarations","svg_stroke_width_range","svg_path_geometry"}

class _UniqueLoader(yaml.SafeLoader): pass
def _mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in result: raise ValueError(f"duplicate YAML key: {key}")
        result[key]=loader.construct_object(value_node,deep=deep)
    return result
_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_mapping)

class Assertion(BaseModel):
    operator: Literal["less_than_or_equal","greater_than_or_equal","equal","not_equal"]
    value: float | int | str
    origin: Literal["user_spec","project_config"]

class AlgorithmSpec(BaseModel):
    id: str
    version: str="1.0"
    parameters: dict[str,Any]=Field(default_factory=dict)

class ProjectRule(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    version: str
    description: str=""
    priority: int=0
    match: dict[str,str]=Field(default_factory=dict)
    requires: list[str]=Field(default_factory=list)
    algorithm: AlgorithmSpec
    assertion: Assertion
    on_missing: Literal["inconclusive","skip"]="inconclusive"
    severity: Literal["info","warning","error"]="error"

def load_project_rules(path: Path) -> list[ProjectRule]:
    try: raw=yaml.load(path.read_text(encoding="utf-8"),Loader=_UniqueLoader)
    except (OSError,yaml.YAMLError,ValueError) as exc: raise ValueError(f"cannot load rule file: {exc}") from exc
    if not isinstance(raw,dict) or raw.get("schema_version")!="1" or not isinstance(raw.get("rules"),list):
        raise ValueError("rule file must contain schema_version: '1' and a rules array")
    try: rules=[ProjectRule.model_validate(item) for item in raw["rules"]]
    except ValidationError as exc: raise ValueError(f"invalid rule definition: {exc.errors(include_url=False)}") from exc
    ids=[rule.id for rule in rules]
    if len(ids)!=len(set(ids)): raise ValueError("duplicate rule IDs")
    for rule in rules:
        if rule.algorithm.id not in SUPPORTED_ALGORITHMS: raise ValueError(f"rule {rule.id}: unsupported algorithm {rule.algorithm.id}")
        unknown=set(rule.match)-{"asset_type","style","feature_role","feature_id"}
        if unknown: raise ValueError(f"rule {rule.id}: unsupported match selectors: {sorted(unknown)}")
    return rules

def match_project_rules(manifest: dict[str,Any], rules: list[ProjectRule]) -> list[tuple[ProjectRule,str|None]]:
    matched=[]
    intent=manifest.get("intent",{})
    intent_values={**intent,"asset_type":intent.get("asset_type",intent.get("type"))}
    for rule in rules:
        asset_selectors={key:value for key,value in rule.match.items() if key in {"asset_type","style"}}
        feature_selectors={key:value for key,value in rule.match.items() if key in {"feature_role","feature_id"}}
        if feature_selectors:
            for feature in manifest.get("features",[]):
                values={**intent_values,"feature_role":feature.get("role"),"feature_id":feature.get("id")}
                if all(values.get(key)==value for key,value in rule.match.items()): matched.append((rule,feature["id"]))
        elif all(intent_values.get(key)==value for key,value in asset_selectors.items()):
            # Project-wide and asset-class rules target manifest-level checks.
            matched.append((rule,None))
    resolved: dict[tuple[Any, ...], tuple[ProjectRule,str|None]] = {}
    for rule,feature_id in matched:
        # Resolve by the measurement target, not selector spelling or parameter
        # values. Different selectors (or parameters) can otherwise write the
        # same manifest property in sequence and silently replace each other.
        if rule.algorithm.id in {"exact_palette_count","quantized_palette_count"}:
            target=feature_id
            measurement: Any="palette_count"
        elif rule.algorithm.id=="mask_intersection_ratio":
            target=None
            pair=rule.algorithm.parameters.get("features",[])
            measurement=("feature_pair",tuple(sorted(str(item) for item in pair))) if isinstance(pair,list) else ("feature_pair",str(pair))
        elif rule.algorithm.id=="svg_path_geometry":
            target=feature_id
            measurement=(rule.algorithm.id,rule.algorithm.parameters.get("metric","max_path_length"))
        else:
            target=feature_id
            measurement=rule.algorithm.id
        key=(target,measurement)
        prior=resolved.get(key)
        if prior and prior[0].priority==rule.priority and (
            prior[0].assertion!=rule.assertion or prior[0].algorithm.parameters!=rule.algorithm.parameters
        ):
            raise ValueError(f"conflicting rules at priority {rule.priority} for the same target and algorithm: {prior[0].id} and {rule.id}")
        if prior is None or rule.priority>prior[0].priority or (rule.priority==prior[0].priority and rule.id<prior[0].id):
            resolved[key]=(rule,feature_id)
    return list(resolved.values())

def apply_project_rules(manifest: dict[str,Any], matched: list[tuple[ProjectRule,str|None]]):
    """Translate supported YAML assertions into the same fixed evaluator contract as inline constraints."""
    result=deepcopy(manifest); features={f["id"]:f for f in result.get("features",[])}; applied=[]
    mapping={
        "quantized_palette_count":("max_palette_colors","COLOR-002","masked_palette_count"),
        "exact_palette_count":("max_palette_colors","COLOR-001","exact_palette_count"),
        "alpha_centroid_offset":("max_normalized_centroid_offset","GEOMETRY-001","alpha_centroid_offset"),
        "alpha_margin_ratio":("min_margin_ratio","GEOMETRY-002","alpha_margin_ratio"),
        "masked_color_uniformity":("max_channel_range","SOLID-001","masked_color_uniformity"),
        "ciede2000_uniformity":("max_delta_e00","SOLID-002","ciede2000_uniformity"),
        "mask_connected_components":("max_connected_components","TOPOLOGY-001","mask_connected_components"),
        "mask_hole_count":("max_holes","TOPOLOGY-002","mask_hole_count"),
        "mask_minimum_width":("min_width_px","GEOMETRY-003","mask_minimum_width"),
        "mask_boundary_distance":("max_boundary_distance_px","GEOMETRY-004","mask_boundary_distance"),
        "mask_iou_dice":("min_iou","GEOMETRY-005","mask_iou_dice"),
        "mask_dice":("min_dice","GEOMETRY-006","mask_dice"),
        "mask_reflection_iou":("min_symmetry_iou","SYMMETRY-001","mask_reflection_iou"),
        "mask_intersection_ratio":("max_overlap_ratio","OVERLAP-001","mask_intersection_ratio"),
        "tile_edge_color_error":("max_tile_edge_error","BACKGROUND-001","tile_edge_color_error"),
        "fft_periodic_energy_ratio":("max_periodic_energy_ratio","BACKGROUND-002","fft_periodic_energy_ratio"),
        "svg_gradient_declarations":("max_svg_gradients","SVG-001","svg_gradient_declarations"),
        "svg_stroke_width_range":("max_svg_stroke_width","SVG-002","svg_stroke_width_range"),
        "svg_path_geometry":(None,"SVG-003","svg_path_geometry"),
    }
    for rule,feature_id in matched:
        key,rule_id,executor=mapping[rule.algorithm.id]; target=result.setdefault("constraints",{}) if feature_id is None else features[feature_id].setdefault("properties",{})
        assertion=rule.assertion
        if rule.algorithm.id=="mask_intersection_ratio":
            if assertion.operator != "less_than_or_equal":
                raise ValueError(f"rule {rule.id}: overlap supports only less_than_or_equal")
            pair=rule.algorithm.parameters.get("features",[])
            if len(pair)!=2: raise ValueError(f"rule {rule.id}: mask_intersection_ratio requires parameters.features with two feature IDs")
            result.setdefault("overlap_constraints",[]).append({"feature_a":pair[0],"feature_b":pair[1],"max_overlap_ratio":assertion.value})
            applied.append({"rule":rule,"feature_id":f"{pair[0]}:{pair[1]}","feature_ids":pair,"rule_id":rule_id,"algorithm_id":executor,"threshold":assertion.value})
            continue
        if key is None:
            p=target
            metric=rule.algorithm.parameters.get("metric","max_path_length")
            if metric not in {"max_path_length","min_path_length","min_path_segments","max_path_aspect_ratio"}:
                raise ValueError(f"rule {rule.id}: unsupported SVG path metric {metric}")
            p["svg_path_id"]=rule.algorithm.parameters.get("path_id")
            key=metric
        supported_operator="greater_than_or_equal" if key.startswith("min_") else "less_than_or_equal"
        if assertion.operator != supported_operator:
            raise ValueError(f"rule {rule.id}: operator {assertion.operator} is unsupported by this policy adapter")
        target[key]=assertion.value
        if rule.algorithm.id=="quantized_palette_count": target["palette_bin_size_lab"]=rule.algorithm.parameters.get("bin_size",[10,10,10])
        if rule.algorithm.id in {"masked_color_uniformity","ciede2000_uniformity"}: target["surface"]="solid"
        if rule.algorithm.id=="tile_edge_color_error": target["seamless_tile"]=True
        applied.append({"rule":rule,"feature_id":feature_id,"rule_id":rule_id,"algorithm_id":executor,"threshold":assertion.value})
    return result,applied
