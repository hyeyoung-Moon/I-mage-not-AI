"""Deterministic algorithms for engine-exported game asset traces (no engine execution)."""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any, Literal
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

class TrackSample(BaseModel):
    model_config=ConfigDict(extra="forbid")
    time: float
    frame_index: int|None=None
    value: Any
    basis_matrix: list[list[float]]|None=None

    @model_validator(mode="after")
    def finite_sample(self):
        if not math.isfinite(self.time): raise ValueError("sample time must be finite")
        if self.basis_matrix is not None:
            n=len(self.basis_matrix)
            if n not in (2,3) or any(len(row)!=n for row in self.basis_matrix) or any(not math.isfinite(x) for row in self.basis_matrix for x in row):
                raise ValueError("sample basis_matrix must be a finite 2x2 or 3x3 matrix")
        return self

class TraceTrack(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    kind: Literal["position","rotation","pose","scalar","custom"]
    units: str
    coordinate_system: str
    coordinate_space: Literal["world","local"]="world"
    basis_matrix: list[list[float]]|None=None
    interpolation: str="linear"
    parent_track_id: str|None=None
    rest_length: float|None=None
    samples: list[TrackSample]

    @model_validator(mode="after")
    def nonempty_contract(self):
        if not all((self.id.strip(),self.kind.strip(),self.units.strip(),self.coordinate_system.strip())):
            raise ValueError("track id, kind, units, and coordinate_system must be non-empty")
        return self

    @model_validator(mode="after")
    def valid_rest_length(self):
        if self.rest_length is not None and (not math.isfinite(self.rest_length) or self.rest_length<=0):
            raise ValueError(f"track {self.id}: rest_length must be finite and positive")
        return self

    @model_validator(mode="after")
    def valid_basis(self):
        if self.basis_matrix is not None:
            n=len(self.basis_matrix)
            if n not in (2,3) or any(len(row)!=n for row in self.basis_matrix) or any(not math.isfinite(x) for row in self.basis_matrix for x in row):
                raise ValueError(f"track {self.id}: basis_matrix must be a finite 2x2 or 3x3 matrix")
        return self

    @model_validator(mode="after")
    def ordered(self):
        ts=[s.time for s in self.samples]
        if any(not math.isfinite(t) for t in ts) or ts!=sorted(ts) or len(ts)!=len(set(ts)):
            raise ValueError(f"track {self.id}: timestamps must be finite, strictly increasing")
        return self

class TraceEvent(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    event_type: str
    time: float
    frame_index: int|None=None

    @model_validator(mode="after")
    def finite_time(self):
        if not math.isfinite(self.time): raise ValueError(f"event {self.id}: time must be finite")
        return self

class ParticleLifetime(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    spawn_time: float
    death_time: float
    position_track_id: str|None=None

    @model_validator(mode="after")
    def valid_lifetime(self):
        if not math.isfinite(self.spawn_time) or not math.isfinite(self.death_time) or self.death_time < self.spawn_time:
            raise ValueError(f"particle {self.id}: invalid lifetime")
        return self

class ContactInterval(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    track_id: str
    start_time: float
    end_time: float

    @model_validator(mode="after")
    def valid_interval(self):
        if not self.id.strip() or not self.track_id.strip() or not math.isfinite(self.start_time) or not math.isfinite(self.end_time) or self.end_time<=self.start_time:
            raise ValueError(f"contact {self.id}: expected non-empty IDs and a finite positive interval")
        return self

class AnchorPair(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    track_a: str
    track_b: str

    @model_validator(mode="after")
    def valid_ids(self):
        if not all(value.strip() for value in (self.id,self.track_a,self.track_b)) or self.track_a==self.track_b:
            raise ValueError("anchor pair requires distinct track IDs and a non-empty ID")
        return self

class FrameLayer(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: str
    path: str
    z_index: int
    region: tuple[int,int,int,int]|None=None

class GameFrame(BaseModel):
    model_config=ConfigDict(extra="forbid")
    time: float
    frame_index: int|None=None
    layers: list[FrameLayer]

    @model_validator(mode="after")
    def valid_frame(self):
        if not math.isfinite(self.time): raise ValueError("frame time must be finite")
        ids=[layer.id for layer in self.layers]
        if len(ids)!=len(set(ids)): raise ValueError("frame layer IDs must be unique")
        if len([x.z_index for x in self.layers])!=len(set(x.z_index for x in self.layers)): raise ValueError("frame z_index values must be unique")
        return self

class GameTrace(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version: Literal["1"]="1"
    engine: Literal["generic","godot","unity"]="generic"
    engine_version: str|None=None
    timebase: str
    random_seed: int|None=None
    tracks: list[TraceTrack]=Field(default_factory=list)
    events: list[TraceEvent]=Field(default_factory=list)
    particles: list[ParticleLifetime]=Field(default_factory=list)
    contacts: list[ContactInterval]=Field(default_factory=list)
    anchors: list[AnchorPair]=Field(default_factory=list)
    frames: list[GameFrame]=Field(default_factory=list)

    @model_validator(mode="after")
    def valid_timebase(self):
        if not self.timebase.strip(): raise ValueError("timebase must be non-empty")
        if self.engine_version is not None and not self.engine_version.strip(): raise ValueError("engine_version must be non-empty when provided")
        return self

    @model_validator(mode="after")
    def unique_ids(self):
        ids=[t.id for t in self.tracks]
        if len(ids)!=len(set(ids)): raise ValueError("track IDs must be unique")
        event_ids=[e.id for e in self.events]
        if len(event_ids)!=len(set(event_ids)): raise ValueError("event IDs must be unique")
        particle_ids=[p.id for p in self.particles]
        if len(particle_ids)!=len(set(particle_ids)): raise ValueError("particle IDs must be unique")
        contact_ids=[c.id for c in self.contacts]
        if len(contact_ids)!=len(set(contact_ids)): raise ValueError("contact IDs must be unique")
        anchor_ids=[a.id for a in self.anchors]
        if len(anchor_ids)!=len(set(anchor_ids)): raise ValueError("anchor IDs must be unique")
        track_ids=set(ids)
        parents={track.id:track.parent_track_id for track in self.tracks if track.parent_track_id}
        for track in self.tracks:
            if track.parent_track_id and track.parent_track_id not in track_ids: raise ValueError(f"track {track.id}: unknown parent_track_id {track.parent_track_id}")
            seen={track.id}; current=track.parent_track_id
            while current in parents:
                if current in seen: raise ValueError(f"track transform hierarchy contains a cycle at {current}")
                seen.add(current); current=parents[current]
        for contact in self.contacts:
            if contact.track_id not in track_ids: raise ValueError(f"contact {contact.id}: unknown track_id {contact.track_id}")
        for anchor in self.anchors:
            if anchor.track_a not in track_ids or anchor.track_b not in track_ids: raise ValueError(f"anchor {anchor.id}: unknown track reference")
        for particle in self.particles:
            if particle.position_track_id and particle.position_track_id not in track_ids: raise ValueError(f"particle {particle.id}: unknown position_track_id {particle.position_track_id}")
        frame_times=[frame.time for frame in self.frames]
        if frame_times!=sorted(frame_times) or len(frame_times)!=len(set(frame_times)): raise ValueError("frame times must be strictly increasing")
        return self

def load_trace(path:Path)->GameTrace:
    try:
        if path.stat().st_size>50*1024*1024: raise ValueError("trace exceeds 50 MiB safety limit")
        raw=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw,dict): raise ValueError("trace root must be a JSON object")
        export_formats={"i_mage_not_ai.godot-export/1":"godot","i_mage_not_ai.unity-export/1":"unity"}
        if raw.get("format") in export_formats:
            if set(raw)!={"format","engine_version","trace"}: raise ValueError("engine export wrapper may only contain format, engine_version, and trace")
            engine=export_formats[raw["format"]]; engine_version=raw.get("engine_version")
            if not isinstance(engine_version,str) or not engine_version.strip(): raise ValueError("engine export wrapper requires a non-empty engine_version")
            payload=raw.get("trace")
            if not isinstance(payload,dict): raise ValueError("engine export wrapper requires a trace object")
            raw={**payload,"engine":engine,"engine_version":engine_version}
        trace=GameTrace.model_validate(raw)
        frame_sizes=set()
        for frame in trace.frames:
            for layer in frame.layers:
                layer_path=Path(layer.path)
                resolved=(path.resolve().parent/layer_path).resolve()
                if layer_path.is_absolute() or not resolved.is_relative_to(path.resolve().parent): raise ValueError(f"frame layer path escapes trace directory: {layer.path}")
                if resolved.suffix.lower()!=".png": raise ValueError(f"frame layer must be a PNG: {layer.path}")
                if not resolved.is_file() or resolved.stat().st_size>50*1024*1024: raise ValueError(f"frame layer is missing or exceeds 50 MiB: {layer.path}")
                with Image.open(resolved) as image:
                    if getattr(image,"n_frames",1)!=1: raise ValueError(f"frame layer must be a single-frame image: {layer.path}")
                    if image.width*image.height>50_000_000: raise ValueError(f"frame layer exceeds 50 million pixels: {layer.path}")
                    if layer.region:
                        x,y,width,height=layer.region
                        if x<0 or y<0 or width<=0 or height<=0 or x+width>image.width or y+height>image.height:
                            raise ValueError(f"frame layer region is outside image bounds: {layer.path}")
                        size=(width,height)
                    else: size=image.size
                    frame_sizes.add((frame.time,size))
        for frame in trace.frames:
            sizes={size for time,size in frame_sizes if time==frame.time}
            if len(sizes)>1: raise ValueError(f"frame {frame.time}: layer dimensions differ")
        return trace
    except (OSError,json.JSONDecodeError,ValidationError) as exc: raise ValueError(f"invalid game trace: {exc}") from exc

def analyze_runtime_evidence(evidence:Any,policy:Any)->dict[str,Any]:
    """Compare observed engine runtime samples against explicit deterministic limits."""
    if not isinstance(evidence,dict) or evidence.get("format")!="i_mage_not_ai.runtime-evidence/1":
        raise ValueError("runtime evidence must use format i_mage_not_ai.runtime-evidence/1")
    if evidence.get("engine") not in {"godot","unity"} or not isinstance(evidence.get("engine_version"),str) or not evidence["engine_version"].strip():
        raise ValueError("runtime evidence requires a Godot/Unity engine and non-empty engine_version")
    if evidence.get("timebase")!="seconds": raise ValueError("runtime evidence timebase must be seconds")
    scenario_id=evidence.get("scenario_id")
    if scenario_id is not None and (not isinstance(scenario_id,str) or not scenario_id.strip()): raise ValueError("scenario_id must be a non-empty string when provided")
    if not isinstance(evidence.get("random_seed"),int) or isinstance(evidence.get("random_seed"),bool): raise ValueError("runtime evidence requires an integer random_seed")
    if not isinstance(evidence.get("callbacks"),list) or not isinstance(evidence.get("particles"),list): raise ValueError("runtime evidence callbacks and particles must be arrays")
    if not isinstance(policy,dict) or set(policy)-{"callbacks","particles"}: raise ValueError("runtime policy may only contain callbacks and particles")
    callback_rules=policy.get("callbacks",{}); particle_rules=policy.get("particles",{})
    if not isinstance(callback_rules,dict) or not isinstance(particle_rules,dict) or not (callback_rules or particle_rules): raise ValueError("runtime policy must define callback and/or particle expectations")
    observed_callbacks:dict[str,list[float]]={}
    for event in evidence["callbacks"]:
        if not isinstance(event,dict) or not isinstance(event.get("name"),str) or not event["name"].strip(): raise ValueError("runtime callback observations require a non-empty name")
        time=event.get("time")
        if not isinstance(time,(int,float)) or isinstance(time,bool) or not math.isfinite(time) or time<0: raise ValueError("runtime callback times must be finite non-negative seconds")
        observed_callbacks.setdefault(event["name"],[]).append(float(time))
    observed_particles:dict[str,list[dict[str,Any]]]={}
    for sample in evidence["particles"]:
        if not isinstance(sample,dict) or not isinstance(sample.get("emitter"),str) or not sample["emitter"].strip(): raise ValueError("runtime particle samples require a non-empty emitter")
        time=sample.get("sample_time"); bounds=sample.get("bounds")
        if not isinstance(time,(int,float)) or isinstance(time,bool) or not math.isfinite(time) or time<0: raise ValueError("particle sample_time must be finite and non-negative")
        if not isinstance(bounds,list) or len(bounds)!=4 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in bounds) or bounds[0]>bounds[2] or bounds[1]>bounds[3]: raise ValueError("particle bounds must be finite [min_x,min_y,max_x,max_y]")
        if "alive_count" in sample and (not isinstance(sample["alive_count"],int) or isinstance(sample["alive_count"],bool) or sample["alive_count"]<0): raise ValueError("alive_count must be a non-negative integer")
        observed_particles.setdefault(sample["emitter"],[]).append(sample)
    findings:list[dict[str,Any]]=[]
    for name,rule in sorted(callback_rules.items()):
        if not isinstance(rule,dict) or set(rule)!={"count","time","tolerance"}: raise ValueError(f"callback policy for {name} requires count, time, and tolerance")
        count=rule["count"]; expected=rule["time"]; tolerance=rule["tolerance"]
        if not isinstance(count,int) or isinstance(count,bool) or count<0 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or v<0 for v in (expected,tolerance)): raise ValueError(f"callback policy for {name} has invalid count/time/tolerance")
        times=observed_callbacks.get(name,[])
        findings.append({"id":f"CALLBACK:{name}:COUNT","algorithm":"runtime_callback_count","version":"1.0","status":"PASS" if len(times)==count else "FAIL","measured":{"count":len(times)},"threshold":{"expected_count":count},"formula":"observed callback invocations == expected count"})
        if len(times)==count:
            error=max((abs(value-float(expected)) for value in times),default=0.0)
            findings.append({"id":f"CALLBACK:{name}:TIME","algorithm":"runtime_callback_timing","version":"1.0","status":"PASS" if error<=tolerance else "FAIL","measured":{"times":times,"max_absolute_error":error,"timebase":"seconds"},"threshold":{"expected_time":expected,"tolerance":tolerance},"formula":"max(abs(observed_time - expected_time)) <= tolerance"})
    for emitter,rule in sorted(particle_rules.items()):
        required={"sample_time","time_tolerance","bounds"}
        optional={"min_alive_count","max_alive_count"}
        if not isinstance(rule,dict) or not required<=set(rule) or set(rule)-required-optional: raise ValueError(f"particle policy for {emitter} requires sample_time, time_tolerance, bounds, and optional alive-count limits")
        if any(not isinstance(rule[key],(int,float)) or isinstance(rule[key],bool) or not math.isfinite(rule[key]) or rule[key]<0 for key in ("sample_time","time_tolerance")): raise ValueError(f"particle policy for {emitter} has invalid sample time")
        expected_bounds=rule["bounds"]
        if not isinstance(expected_bounds,list) or len(expected_bounds)!=4 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in expected_bounds) or expected_bounds[0]>expected_bounds[2] or expected_bounds[1]>expected_bounds[3]: raise ValueError(f"particle policy bounds for {emitter} must be finite [min_x,min_y,max_x,max_y]")
        for key in optional & set(rule):
            if not isinstance(rule[key],int) or isinstance(rule[key],bool) or rule[key]<0: raise ValueError(f"{key} for {emitter} must be a non-negative integer")
        candidates=observed_particles.get(emitter,[])
        if not candidates:
            findings.append({"id":f"PARTICLE:{emitter}","algorithm":"runtime_particle_state","version":"1.0","status":"INCONCLUSIVE","measured":None,"threshold":rule,"reason":"no live particle sample for this emitter"})
            continue
        sample=min(candidates,key=lambda item:abs(float(item["sample_time"])-float(rule["sample_time"])))
        time_error=abs(float(sample["sample_time"])-float(rule["sample_time"]))
        if time_error>rule["time_tolerance"]:
            findings.append({"id":f"PARTICLE:{emitter}:TIME","algorithm":"runtime_particle_sample_time","version":"1.0","status":"FAIL","measured":{"sample_time":sample["sample_time"],"absolute_error":time_error},"threshold":{"sample_time":rule["sample_time"],"tolerance":rule["time_tolerance"]}})
        actual=sample["bounds"]
        within=actual[0]>=expected_bounds[0] and actual[1]>=expected_bounds[1] and actual[2]<=expected_bounds[2] and actual[3]<=expected_bounds[3]
        findings.append({"id":f"PARTICLE:{emitter}:BOUNDS","algorithm":"runtime_particle_bounds","version":"1.0","status":"PASS" if within else "FAIL","measured":{"bounds":actual,"sample_time":sample["sample_time"],"units":sample.get("bounds_units")},"threshold":{"bounds":expected_bounds},"formula":"observed particle AABB contained by declared bounds"})
        if "min_alive_count" in rule or "max_alive_count" in rule:
            alive=sample.get("alive_count"); minimum=rule.get("min_alive_count",0); maximum=rule.get("max_alive_count",2**31-1)
            findings.append({"id":f"PARTICLE:{emitter}:COUNT","algorithm":"runtime_particle_alive_count","version":"1.0","status":"INCONCLUSIVE" if alive is None else ("PASS" if minimum<=alive<=maximum else "FAIL"),"measured":{"alive_count":alive},"threshold":{"min":minimum,"max":maximum},"formula":"min_alive_count <= observed live count <= max_alive_count"})
    status="FAIL" if any(f["status"]=="FAIL" for f in findings) else ("INCONCLUSIVE" if any(f["status"]=="INCONCLUSIVE" for f in findings) else "PASS")
    return {"schema_version":"0.5","asset_type":"runtime_evidence","status":status,"completeness":"COMPLETE" if findings and all(f["status"] in {"PASS","FAIL"} for f in findings) else "INCOMPLETE","engine":evidence["engine"],"engine_version":evidence["engine_version"],"scenario_id":scenario_id,"timebase":evidence["timebase"],"random_seed":evidence["random_seed"],"results":findings}

def aggregate_game_gate(review_report:Any,runtime_report:Any,render_report:Any)->dict[str,Any]:
    """Require game trace, observed runtime, and rendered frame evidence to pass together."""
    required: dict[str, Any]={"game_trace":review_report,"runtime_evidence":runtime_report,"render_evidence":render_report}
    findings: list[dict[str, Any]]=[]; scenario_ids: list[tuple[str, Any]]=[]
    for expected_type,report in required.items():
        if not isinstance(report,dict):
            findings.append({"gate":expected_type,"status":"INCONCLUSIVE","reason":"report must be a JSON object"}); continue
        actual_type=report.get("asset_type")
        scenario=report.get("scenario_id")
        if scenario is not None: scenario_ids.append((expected_type,scenario))
        if actual_type!=expected_type:
            findings.append({"gate":expected_type,"status":"INCONCLUSIVE","reason":f"expected asset_type={expected_type}, received {actual_type!r}"}); continue
        if report.get("status") not in {"PASS","FAIL","INCONCLUSIVE"} or report.get("completeness") not in {"COMPLETE","INCOMPLETE"}:
            findings.append({"gate":expected_type,"status":"INCONCLUSIVE","reason":"report lacks a recognized status/completeness"}); continue
        state="PASS" if report["status"]=="PASS" and report["completeness"]=="COMPLETE" else report["status"]
        if state=="PASS" and expected_type in {"runtime_evidence","render_evidence"} and not scenario:
            state="INCONCLUSIVE"
        findings.append({"gate":expected_type,"status":state,"scenario_id":scenario,
                         "source_status":report["status"],"source_completeness":report["completeness"]})
    distinct={scenario for _,scenario in scenario_ids}
    if len(distinct)>1:
        findings.append({"gate":"scenario_alignment","status":"INCONCLUSIVE","reason":"trace, runtime, and render reports refer to different scenarios","scenario_ids":dict(scenario_ids)})
    elif len(scenario_ids)<3:
        findings.append({"gate":"scenario_alignment","status":"INCONCLUSIVE","reason":"all three reports must declare a scenario_id"})
    else:
        findings.append({"gate":"scenario_alignment","status":"PASS","scenario_id":next(iter(distinct))})
    complete=all(row["status"] in {"PASS","FAIL"} for row in findings)
    status="FAIL" if any(row["status"]=="FAIL" for row in findings) else ("PASS" if complete else "INCONCLUSIVE")
    return {"schema_version":"0.5","asset_type":"game_gate","status":status,"completeness":"COMPLETE" if complete else "INCOMPLETE",
            "coverage":{"required":len(findings),"completed":sum(row["status"] in {"PASS","FAIL"} for row in findings),
                        "missing":[row["gate"] for row in findings if row["status"] not in {"PASS","FAIL"}]},"results":findings}

def load_runtime_evidence(path:Path)->dict[str,Any]:
    try:
        evidence=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(evidence,dict): raise ValueError("runtime evidence root must be a JSON object")
        return evidence
    except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"invalid runtime evidence: {exc}") from exc

def load_runtime_policy(path:Path)->dict[str,Any]:
    try:
        policy=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(policy,dict): raise ValueError("runtime policy root must be a JSON object")
        return policy
    except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"invalid runtime policy: {exc}") from exc

def load_policy(path:Path|None)->dict[str,Any]:
    if path is None: return {}
    try: policy=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"invalid game policy: {exc}") from exc
    return validate_policy(policy)

def validate_policy(policy:Any)->dict[str,Any]:
    if not isinstance(policy,dict): raise ValueError("game policy must be a JSON object")
    allowed={"max_position_step","max_velocity","max_acceleration","max_jerk","max_loop_boundary_difference","max_pose_loop_boundary_distance","max_rotation_step_degrees","max_rotation_loop_difference_degrees","expected_event_times","max_event_timing_error","max_particle_lifetime","max_bone_length_deviation","max_foot_sliding_distance","max_anchor_distance","particle_bounds","max_alpha_occlusion_ratio"}
    unknown=set(policy)-allowed
    if unknown: raise ValueError(f"unknown game policy keys: {sorted(unknown)}")
    for key,value in policy.items():
        if value is None and key not in {"expected_event_times","particle_bounds"}:
            # A null limit requests a measurement without inventing a pass/fail threshold.
            continue
        if key=="expected_event_times":
            if not isinstance(value,dict) or any(not isinstance(k,str) or not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for k,v in value.items()):
                raise ValueError("expected_event_times must map event IDs to finite numeric times")
        elif key=="particle_bounds":
            if not isinstance(value,dict) or set(value)!={"min","max","units","coordinate_system"}:
                raise ValueError("particle_bounds must contain min/max vectors, units, and coordinate_system")
            if not isinstance(value["units"],str) or not value["units"].strip() or not isinstance(value["coordinate_system"],str) or not value["coordinate_system"].strip():
                raise ValueError("particle_bounds units and coordinate_system must be non-empty strings")
            try: low=[float(x) for x in value["min"]]; high=[float(x) for x in value["max"]]
            except (TypeError,ValueError): raise ValueError("particle_bounds min/max must be numeric vectors")
            if len(low) not in (2,3) or len(low)!=len(high) or not all(math.isfinite(x) for x in low+high) or any(a>b for a,b in zip(low,high)):
                raise ValueError("particle_bounds must have matching 2D/3D vectors with min <= max")
        elif key=="max_alpha_occlusion_ratio":
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or not 0<=value<=1:
                raise ValueError("max_alpha_occlusion_ratio must be between 0 and 1")
        elif not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:
            raise ValueError(f"{key} must be a finite non-negative number")
        elif key in {"max_rotation_step_degrees","max_rotation_loop_difference_degrees"} and value>360:
            raise ValueError(f"{key} must not exceed 360 degrees")
    return policy

def _dist(a,b):
    if not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or not a or len(a)!=len(b):
        raise ValueError("samples must be non-empty vectors of equal size")
    if any(isinstance(x,bool) for x in (*a,*b)): raise ValueError("position samples must be numeric vectors")
    values=[float(x) for x in (*a,*b)]
    if not all(math.isfinite(x) for x in values): raise ValueError("position samples must be finite")
    result=math.hypot(*(float(x)-float(y) for x,y in zip(a,b)))
    if not math.isfinite(result): raise ValueError("position distance overflowed the finite numeric range")
    return result

def _finding(identifier,algorithm,status,measured,threshold,formula,**extra):
    return {"id":identifier,"algorithm":algorithm,"version":"1.0","status":status,"measured":measured,"threshold":threshold,"formula":formula,**extra}

def _quaternion_distance_degrees(a,b):
    if not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or len(a)!=4 or len(b)!=4: raise ValueError("rotation samples must be [x,y,z,w] quaternions")
    qa=[float(x) for x in a]; qb=[float(x) for x in b]
    if not all(math.isfinite(x) for x in qa+qb): raise ValueError("quaternion values must be finite")
    # Scale before taking norms: finite components can still overflow when squared.
    scale_a=max(abs(x) for x in qa); scale_b=max(abs(x) for x in qb)
    if scale_a==0 or scale_b==0: raise ValueError("quaternion norm must be positive")
    unit_a=[x/scale_a for x in qa]; unit_b=[x/scale_b for x in qb]
    na=math.sqrt(sum(x*x for x in unit_a)); nb=math.sqrt(sum(x*x for x in unit_b))
    cosine=abs(sum(x*y for x,y in zip(unit_a,unit_b))/(na*nb))
    return math.degrees(2*math.acos(min(1.0,max(-1.0,cosine))))

def _flatten_pose(value):
    if isinstance(value,dict): return [number for key in sorted(value) for number in _flatten_pose(value[key])]
    if isinstance(value,(list,tuple)): return [number for item in value for number in _flatten_pose(item)]
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError("pose values must contain only finite numbers")
    return [float(value)]

def _validate_pose_shape(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        if not isinstance(a, dict) or not isinstance(b, dict) or set(a) != set(b):
            raise ValueError("pose sample joint keys differ")
        for key in a:
            _validate_pose_shape(a[key], b[key])
    elif isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != len(b):
            raise ValueError("pose sample vector dimensions differ")
        for left, right in zip(a, b):
            _validate_pose_shape(left, right)

def _pose_vector_distance(a,b):
    _validate_pose_shape(a,b)
    if isinstance(a,dict):
        return _dist([x for key in sorted(a) for x in _flatten_pose(a[key])],
                     [x for key in sorted(b) for x in _flatten_pose(b[key])])
    return _dist(_flatten_pose(a),_flatten_pose(b))

def _identity(n): return [[1.0 if i==j else 0.0 for j in range(n)] for i in range(n)]
def _mat_vec(matrix,vector): return [sum(row[j]*vector[j] for j in range(len(vector))) for row in matrix]
def _mat_mul(a,b): return [[sum(a[i][k]*b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]

def _world_transforms(track_id:str,tracks:dict[str,TraceTrack],cache:dict[str,tuple[list[list[float]],list[list[list[float]]]]],active:set[str]|None=None):
    if track_id in cache: return cache[track_id]
    active=set() if active is None else active
    if track_id in active: raise ValueError("transform hierarchy contains a cycle")
    active.add(track_id); track=tracks[track_id]
    if track.kind!="position": raise ValueError(f"track {track_id} is not a position track")
    positions=[]; bases=[]
    for sample in track.samples:
        if not isinstance(sample.value,(list,tuple)) or len(sample.value) not in (2,3): raise ValueError(f"track {track_id} has malformed position sample")
        vector=[float(value) for value in sample.value]
        if not all(math.isfinite(value) for value in vector): raise ValueError(f"track {track_id} has non-finite position sample")
        matrix=sample.basis_matrix or track.basis_matrix or _identity(len(vector))
        if len(matrix)!=len(vector): raise ValueError(f"track {track_id} basis dimensionality differs from position")
        positions.append(vector); bases.append(matrix)
    if track.coordinate_space=="local" and track.parent_track_id:
        parent=tracks[track.parent_track_id]
        if parent.units!=track.units or parent.coordinate_system!=track.coordinate_system: raise ValueError(f"track {track_id} and parent use incompatible units or coordinate systems")
        if [sample.time for sample in parent.samples]!=[sample.time for sample in track.samples]: raise ValueError(f"track {track_id} and parent samples are not synchronized")
        parent_positions,parent_bases=_world_transforms(parent.id,tracks,cache,active)
        world_positions=[]; world_bases=[]
        for position,basis,parent_position,parent_basis in zip(positions,bases,parent_positions,parent_bases):
            if len(position)!=len(parent_position) or len(parent_basis)!=len(basis): raise ValueError(f"track {track_id} and parent dimensions differ")
            transformed_offset=_mat_vec(parent_basis,position)
            world_positions.append([a+b for a,b in zip(parent_position,transformed_offset)])
            world_bases.append(_mat_mul(parent_basis,basis))
        positions,bases=world_positions,world_bases
    active.remove(track_id); cache[track_id]=(positions,bases)
    return positions,bases

def analyze_trace(trace:GameTrace, policy:dict[str,Any]|None=None,source_dir:Path|None=None)->dict[str,Any]:
    policy=validate_policy(policy or {}); findings=[]; tracks={t.id:t for t in trace.tracks}; position_cache: dict[str, tuple[list[list[float]], list[list[list[float]]]]] = {}
    for track in trace.tracks:
        if track.kind != "position": continue
        try: values=_world_transforms(track.id,tracks,position_cache)[0]
        except (ValueError,TypeError) as exc:
            findings.append(_finding(f"MOTION:{track.id}","position_difference","INCONCLUSIVE",None,policy.get("max_position_step"),"Euclidean distance between consecutive world-space samples",reason=str(exc))); continue
        times=[sample.time for sample in track.samples]
        metric={"sample_count":len(values),"units":track.units,"timebase":trace.timebase,"coordinate_system":track.coordinate_system}
        threshold=policy.get("max_position_step")
        if len(values)<2:
            findings.append(_finding(f"MOTION:{track.id}","position_difference","INCONCLUSIVE",
                {**metric,"max_step":None,"mean_step":None},threshold,
                "Euclidean distance between consecutive world-space samples",
                reason="at least two position samples are required"))
            for key,algorithm,identifier,required in (
                ("max_velocity","velocity_magnitude","VELOCITY",2),
                ("max_acceleration","acceleration_magnitude","ACCEL",3),
                ("max_jerk","jerk_magnitude","JERK",4)):
                if key in policy:
                    findings.append(_finding(f"{identifier}:{track.id}",algorithm,"INCONCLUSIVE",None,policy[key],
                        f"position samples required for {algorithm}",
                        reason=f"at least {required} position samples are required"))
            continue
        try: steps=[_dist(a,b) for a,b in zip(values,values[1:])]
        except (ValueError,TypeError):
            findings.append({"id":f"MOTION:{track.id}","status":"INCONCLUSIVE","reason":"unsupported or malformed vector samples"}); continue
        max_step=max(steps)
        mean_step=max_step*(sum(step/max_step for step in steps)/len(steps)) if max_step else 0.0
        if not math.isfinite(mean_step):
            findings.append(_finding(f"MOTION:{track.id}","position_difference","INCONCLUSIVE",None,threshold,
                "Euclidean distance between consecutive world-space samples",reason="mean step exceeds the finite numeric range")); continue
        metric={**metric,"max_step":max_step,"mean_step":mean_step}
        findings.append({"id":f"MOTION:{track.id}","algorithm":"position_difference","version":"1.0",
                         "status":"INCONCLUSIVE" if threshold is None else ("PASS" if metric["max_step"]<=threshold else "FAIL"),
                         "measured":metric,"threshold":threshold,"formula":"Euclidean distance between consecutive world-space samples"})
        velocity_vectors=[[(b-a)/(tb-ta) for a,b in zip(p,q)] for p,q,ta,tb in zip(values,values[1:],times,times[1:])]
        velocity=[math.hypot(*vector) for vector in velocity_vectors]
        velocity_finite=all(math.isfinite(axis) for vector in velocity_vectors for axis in vector) and all(math.isfinite(value) for value in velocity)
        limit=policy.get("max_velocity")
        if "max_velocity" in policy:
            if velocity_finite:
                findings.append({"id":f"VELOCITY:{track.id}","algorithm":"velocity_magnitude","version":"1.0",
                                 "status":"INCONCLUSIVE" if limit is None else ("PASS" if max(velocity)<=limit else "FAIL"),
                                 "measured":{"max":max(velocity),"units":f"{track.units}/{trace.timebase}"},"threshold":limit,"formula":"distance / elapsed time"})
            else:
                findings.append(_finding(f"VELOCITY:{track.id}","velocity_magnitude","INCONCLUSIVE",None,limit,
                    "distance / elapsed time",reason="derived velocity exceeds the finite numeric range"))
        midpoint=[a+(b-a)/2 for a,b in zip(times,times[1:])]
        acceleration_vectors=[]
        acceleration=[]
        acceleration_unrepresentable=False
        midpoint_valid=all(math.isfinite(value) for value in midpoint) and all(a < b for a,b in zip(midpoint,midpoint[1:]))
        if velocity_finite and midpoint_valid and len(velocity_vectors)>=2:
            acceleration_vectors=[[(b-a)/(tb-ta) for a,b in zip(v,w)] for v,w,ta,tb in zip(velocity_vectors,velocity_vectors[1:],midpoint,midpoint[1:])]
            acceleration=[math.hypot(*vector) for vector in acceleration_vectors]
            if not all(math.isfinite(axis) for vector in acceleration_vectors for axis in vector) or not all(math.isfinite(value) for value in acceleration):
                acceleration_unrepresentable=True
                acceleration_vectors=[]
                acceleration=[]
        limit=policy.get("max_acceleration")
        if "max_acceleration" in policy:
            if acceleration:
                findings.append(_finding(f"ACCEL:{track.id}","acceleration_magnitude","INCONCLUSIVE" if limit is None else ("PASS" if max(acceleration)<=limit else "FAIL"),
                    {"max":max(acceleration),"units":f"{track.units}/{trace.timebase}^2"},limit,"max Euclidean norm((v[i+1]-v[i]) / delta_time_between_segment_midpoints)"))
            else:
                findings.append(_finding(f"ACCEL:{track.id}","acceleration_magnitude","INCONCLUSIVE",None,limit,
                    "max Euclidean norm((v[i+1]-v[i]) / delta_time_between_segment_midpoints)",
                    reason="derived acceleration exceeds the finite numeric range" if acceleration_unrepresentable else "at least three position samples are required"))
        limit=policy.get("max_jerk")
        if "max_jerk" in policy:
            if len(acceleration_vectors)>=2:
                accel_mid=[(a+b)/2 for a,b in zip(midpoint,midpoint[1:])]
                jerk_vectors=[[(b-a)/(tb-ta) for a,b in zip(v,w)] for v,w,ta,tb in zip(acceleration_vectors,acceleration_vectors[1:],accel_mid,accel_mid[1:])]
                jerk=[math.hypot(*vector) for vector in jerk_vectors]
                if all(math.isfinite(axis) for vector in jerk_vectors for axis in vector) and all(math.isfinite(value) for value in jerk):
                    findings.append(_finding(f"JERK:{track.id}","jerk_magnitude","INCONCLUSIVE" if limit is None else ("PASS" if max(jerk)<=limit else "FAIL"),
                        {"max":max(jerk),"units":f"{track.units}/{trace.timebase}^3"},limit,"max Euclidean norm((a[i+1]-a[i]) / delta_time_between_acceleration_midpoints)"))
                else:
                    findings.append(_finding(f"JERK:{track.id}","jerk_magnitude","INCONCLUSIVE",None,limit,
                        "max Euclidean norm((a[i+1]-a[i]) / delta_time_between_acceleration_midpoints)",
                        reason="derived jerk exceeds the finite numeric range"))
            else:
                findings.append(_finding(f"JERK:{track.id}","jerk_magnitude","INCONCLUSIVE",None,limit,
                    "max Euclidean norm((a[i+1]-a[i]) / delta_time_between_acceleration_midpoints)",
                    reason="at least four position samples are required"))
    for track in trace.tracks:
        if track.kind=="rotation":
            try: steps=[_quaternion_distance_degrees(a.value,b.value) for a,b in zip(track.samples,track.samples[1:])]
            except (ValueError,TypeError) as exc:
                findings.append(_finding(f"ROTATION:{track.id}","rotation_distance","INCONCLUSIVE",None,policy.get("max_rotation_step_degrees"),"shortest quaternion angular distance in degrees",reason=str(exc))); continue
            if "max_rotation_step_degrees" in policy and len(track.samples)<2:
                findings.append(_finding(f"ROTATION:{track.id}","rotation_distance","INCONCLUSIVE",None,
                    policy["max_rotation_step_degrees"],"maximum quaternion angular distance between consecutive samples",
                    reason="at least two rotation samples are required"))
            elif "max_rotation_step_degrees" in policy:
                limit=policy["max_rotation_step_degrees"]
                findings.append(_finding(f"ROTATION:{track.id}","rotation_distance","INCONCLUSIVE" if limit is None else ("PASS" if max(steps)<=limit else "FAIL"),
                    {"max_degrees":max(steps),"sample_count":len(track.samples)},limit,"max(2*acos(abs(dot(q[i],q[i+1])))) in degrees"))
            if "max_rotation_loop_difference_degrees" in policy and len(track.samples)<2:
                findings.append(_finding(f"ROTATION-LOOP:{track.id}","rotation_distance","INCONCLUSIVE",None,
                    policy["max_rotation_loop_difference_degrees"],"shortest quaternion angular distance between first and last samples",
                    reason="at least two rotation samples are required"))
            elif "max_rotation_loop_difference_degrees" in policy:
                try: loop=_quaternion_distance_degrees(track.samples[0].value,track.samples[-1].value)
                except (ValueError,TypeError) as exc:
                    findings.append(_finding(f"ROTATION-LOOP:{track.id}","rotation_distance","INCONCLUSIVE",None,policy["max_rotation_loop_difference_degrees"],"shortest quaternion angular distance in degrees",reason=str(exc))); continue
                limit=policy["max_rotation_loop_difference_degrees"]
                findings.append(_finding(f"ROTATION-LOOP:{track.id}","rotation_distance","INCONCLUSIVE" if limit is None else ("PASS" if loop<=limit else "FAIL"),{"degrees":loop},limit,"shortest quaternion angular distance between first and last samples"))
        if track.kind=="pose" and "max_pose_loop_boundary_distance" in policy and len(track.samples)<2:
            findings.append(_finding(f"POSE-LOOP:{track.id}","pose_boundary_distance","INCONCLUSIVE",None,
                policy["max_pose_loop_boundary_distance"],"Euclidean distance between flattened first and last pose samples",
                reason="at least two pose samples are required"))
        elif track.kind=="pose" and "max_pose_loop_boundary_distance" in policy:
            limit=policy["max_pose_loop_boundary_distance"]
            try:
                distance=_pose_vector_distance(track.samples[0].value,track.samples[-1].value)
                findings.append(_finding(f"POSE-LOOP:{track.id}","pose_boundary_distance","INCONCLUSIVE" if limit is None else ("PASS" if distance<=limit else "FAIL"),{"distance":distance,"units":track.units},limit,"Euclidean distance between flattened first and last pose samples"))
            except (ValueError,TypeError) as exc:
                findings.append(_finding(f"POSE-LOOP:{track.id}","pose_boundary_distance","INCONCLUSIVE",None,limit,"Euclidean distance between flattened first and last pose samples",reason=str(exc)))
    # A bone is represented by child and parent position tracks with matching timestamps.
    for child in trace.tracks:
        if child.kind!="position" or not child.parent_track_id or child.rest_length is None: continue
        parent=tracks[child.parent_track_id]; limit=policy.get("max_bone_length_deviation")
        if parent.kind!="position" or parent.coordinate_system!=child.coordinate_system or parent.units!=child.units or [s.time for s in parent.samples]!=[s.time for s in child.samples] or not child.samples:
            findings.append(_finding(f"BONE:{child.id}","bone_length_deviation","INCONCLUSIVE",None,limit,"|distance(parent,child)-rest_length| / rest_length",reason="paired position samples are missing or unsynchronized")); continue
        try:
            parent_world=_world_transforms(parent.id,tracks,position_cache)[0]; child_world=_world_transforms(child.id,tracks,position_cache)[0]
            lengths=[_dist(a,b) for a,b in zip(parent_world,child_world)]
        except (ValueError,TypeError):
            findings.append(_finding(f"BONE:{child.id}","bone_length_deviation","INCONCLUSIVE",None,limit,"|distance(parent,child)-rest_length| / rest_length",reason="bone samples must be finite matching 2D/3D vectors")); continue
        deviation=max(abs(length-child.rest_length)/child.rest_length for length in lengths)
        findings.append(_finding(f"BONE:{child.id}","bone_length_deviation","INCONCLUSIVE" if limit is None else ("PASS" if deviation<=limit else "FAIL"),
                                 {"max_relative_deviation":deviation,"rest_length":child.rest_length,"units":child.units},limit,"max(|distance(parent,child)-rest_length| / rest_length)"))
    for contact in trace.contacts:
        track=tracks[contact.track_id]; samples=track.samples
        indices=[i for i,s in enumerate(samples) if contact.start_time<=s.time<=contact.end_time]
        interval=[samples[i] for i in indices]
        aligned=bool(interval and interval[0].time==contact.start_time and interval[-1].time==contact.end_time)
        if track.kind!="position" or not aligned:
            findings.append(_finding(f"FOOTSLIDE:{contact.id}","foot_sliding_distance","INCONCLUSIVE",None,policy.get("max_foot_sliding_distance"),"sum of consecutive foot-position distances during declared contact",reason="position track must include samples exactly at both contact boundaries")); continue
        try:
            positions=_world_transforms(track.id,tracks,position_cache)[0]
            distance=sum(_dist(positions[a],positions[b]) for a,b in zip(indices,indices[1:]))
        except (ValueError,TypeError):
            findings.append(_finding(f"FOOTSLIDE:{contact.id}","foot_sliding_distance","INCONCLUSIVE",None,policy.get("max_foot_sliding_distance"),"sum of consecutive foot-position distances during declared contact",reason="contact samples must be finite matching 2D/3D vectors")); continue
        limit=policy.get("max_foot_sliding_distance")
        findings.append(_finding(f"FOOTSLIDE:{contact.id}","foot_sliding_distance","INCONCLUSIVE" if limit is None else ("PASS" if distance<=limit else "FAIL"),
                                 {"distance":distance,"duration":contact.end_time-contact.start_time,"units":track.units},limit,"sum of Euclidean position steps during contact"))
    for anchor in trace.anchors:
        a=tracks[anchor.track_a]; b=tracks[anchor.track_b]; limit=policy.get("max_anchor_distance")
        if a.kind!="position" or b.kind!="position" or not a.samples or not b.samples or a.units!=b.units or a.coordinate_system!=b.coordinate_system or [s.time for s in a.samples]!=[s.time for s in b.samples]:
            findings.append(_finding(f"ANCHOR:{anchor.id}","anchor_distance","INCONCLUSIVE",None,limit,"max world-space distance between paired anchors",reason="anchor tracks must be synchronized positions with matching units and coordinate systems")); continue
        try:
            aw=_world_transforms(a.id,tracks,position_cache)[0]; bw=_world_transforms(b.id,tracks,position_cache)[0]
            distance=max((_dist(x,y) for x,y in zip(aw,bw)),default=0.0)
        except (ValueError,TypeError) as exc:
            findings.append(_finding(f"ANCHOR:{anchor.id}","anchor_distance","INCONCLUSIVE",None,limit,"max world-space distance between paired anchors",reason=str(exc))); continue
        findings.append(_finding(f"ANCHOR:{anchor.id}","anchor_distance","INCONCLUSIVE" if limit is None else ("PASS" if distance<=limit else "FAIL"),
            {"max_distance":distance,"units":a.units,"sample_count":len(aw)},limit,"max Euclidean distance between paired world-space anchor samples"))
    for track in trace.tracks:
        if track.kind!="position" or "max_loop_boundary_difference" not in policy: continue
        if len(track.samples)<2:
            findings.append(_finding(f"LOOP:{track.id}","loop_boundary_difference","INCONCLUSIVE",None,
                policy["max_loop_boundary_difference"],"world-space distance between first and last samples",
                reason="at least two position samples are required")); continue
        try:
            positions=_world_transforms(track.id,tracks,position_cache)[0]; loop_error=_dist(positions[0],positions[-1])
        except (ValueError,TypeError):
            findings.append(_finding(f"LOOP:{track.id}","loop_boundary_difference","INCONCLUSIVE",None,policy.get("max_loop_boundary_difference"),"world-space distance between first and last samples",reason="position transforms could not be composed")); continue
        limit=policy.get("max_loop_boundary_difference")
        findings.append({"id":f"LOOP:{track.id}","algorithm":"loop_boundary_difference","version":"1.0",
                         "status":"INCONCLUSIVE" if limit is None else ("PASS" if loop_error<=limit else "FAIL"),
                         "measured":{"distance":loop_error,"units":track.units,"coordinate_system":track.coordinate_system},"threshold":limit,"formula":"Euclidean distance between first and last world-space samples"})
    event_policy=policy.get("expected_event_times",{})
    observed_event_ids={event.id for event in trace.events}
    for missing_id in sorted(set(event_policy)-observed_event_ids):
        findings.append({"id":f"EVENT:{missing_id}","algorithm":"event_timing_difference","version":"1.0","status":"INCONCLUSIVE",
                         "measured":None,"threshold":policy.get("max_event_timing_error"),"reason":"expected event is absent from trace",
                         "formula":"observed event required by expected_event_times"})
    for event in trace.events:
        expected=event_policy.get(event.id)
        measured=abs(event.time-expected) if expected is not None else None
        tolerance=policy.get("max_event_timing_error")
        status="INCONCLUSIVE" if expected is None or tolerance is None else ("PASS" if measured<=tolerance else "FAIL")
        findings.append({"id":f"EVENT:{event.id}","algorithm":"event_timing_difference","version":"1.0","status":status,
                         "measured":{"time":event.time,"absolute_error":measured,"timebase":trace.timebase},"threshold":tolerance,"formula":"abs(observed event time - expected event time)"})
    max_lifetime=policy.get("max_particle_lifetime")
    for particle in trace.particles if "max_particle_lifetime" in policy else ():
        lifetime=particle.death_time-particle.spawn_time
        findings.append({"id":f"PARTICLE:{particle.id}","algorithm":"particle_lifetime","version":"1.0",
                         "status":"INCONCLUSIVE" if max_lifetime is None else ("PASS" if lifetime<=max_lifetime else "FAIL"),
                         "measured":{"lifetime":lifetime,"timebase":trace.timebase},"threshold":max_lifetime,"formula":"death_time - spawn_time"})
        if "particle_bounds" in policy:
            bounds=policy["particle_bounds"]; candidate_track=tracks.get(particle.position_track_id or "")
            samples=[s for s in candidate_track.samples if particle.spawn_time<=s.time<=particle.death_time] if candidate_track else []
            out=[]; bounds_error=None
            try:
                if candidate_track and (candidate_track.units!=bounds["units"] or candidate_track.coordinate_system!=bounds["coordinate_system"]):
                    raise ValueError("particle track coordinate system or units differ from declared bounds")
                if candidate_track:
                    world=_world_transforms(candidate_track.id,tracks,position_cache)[0]
                    for sample_index,sample in enumerate(candidate_track.samples):
                        if particle.spawn_time<=sample.time<=particle.death_time:
                            point=world[sample_index]
                            if len(point)!=len(bounds["min"]): raise ValueError("coordinate dimensionality differs from bounds")
                            if any(axis<float(bounds["min"][index]) or axis>float(bounds["max"][index]) for index,axis in enumerate(point)): out.append(sample.time)
                state="INCONCLUSIVE" if not samples else ("FAIL" if out else "PASS")
            except (TypeError,ValueError) as exc: state="INCONCLUSIVE"; bounds_error=str(exc)
            findings.append(_finding(f"BOUNDS:{particle.id}","particle_emitter_bounds",state,{"out_of_bounds_sample_count":len(out)} if samples else None,
                                     bounds,"particle positions remain inside declared AABB",reason=bounds_error or ("position samples unavailable or malformed" if not samples else None)))
    occlusion_limit=policy.get("max_alpha_occlusion_ratio")
    for frame_index,frame in enumerate(trace.frames):
        if len(frame.layers)<2: continue
        if source_dir is None:
            findings.append(_finding(f"OCCLUSION:{frame_index}","alpha_weighted_occlusion","INCONCLUSIVE",None,occlusion_limit,"alpha-weighted overlap of each back layer under all front layers",reason="frame source directory is unavailable")); continue
        alpha_layers=[]
        for layer in sorted(frame.layers,key=lambda item:item.z_index):
            with Image.open((source_dir/layer.path).resolve()) as image:
                rgba=image.convert("RGBA")
                if layer.region:
                    x,y,width,height=layer.region; rgba=rgba.crop((x,y,x+width,y+height))
                alpha_layers.append((layer,np.asarray(rgba.getchannel("A"),dtype=np.float64)/255.0))
        for back_index,(back_layer,back_alpha) in enumerate(alpha_layers[:-1]):
            denominator=float(back_alpha.sum())
            transmittance=np.ones_like(back_alpha)
            for _,front_alpha in alpha_layers[back_index+1:]: transmittance*=1.0-front_alpha
            ratio=float((back_alpha*(1.0-transmittance)).sum()/denominator) if denominator else None
            state="INCONCLUSIVE" if ratio is None or occlusion_limit is None else ("PASS" if ratio<=occlusion_limit else "FAIL")
            findings.append(_finding(f"OCCLUSION:{frame_index}:{back_layer.id}","alpha_weighted_occlusion",state,
                {"occlusion_ratio":ratio,"frame_time":frame.time,"layer_id":back_layer.id,"z_index":back_layer.z_index},occlusion_limit,
                "sum(back_alpha * (1 - product(1 - front_alpha))) / sum(back_alpha)"))
    required_metrics={"max_position_step":"position_difference","max_velocity":"velocity_magnitude","max_acceleration":"acceleration_magnitude","max_jerk":"jerk_magnitude","max_loop_boundary_difference":"loop_boundary_difference","max_event_timing_error":"event_timing_difference","max_particle_lifetime":"particle_lifetime","max_bone_length_deviation":"bone_length_deviation","max_foot_sliding_distance":"foot_sliding_distance","max_anchor_distance":"anchor_distance","max_alpha_occlusion_ratio":"alpha_weighted_occlusion","max_pose_loop_boundary_distance":"pose_boundary_distance","max_rotation_step_degrees":"rotation_distance","max_rotation_loop_difference_degrees":"rotation_distance","particle_bounds":"particle_emitter_bounds"}
    present={f.get("algorithm") for f in findings}
    for key,algorithm in required_metrics.items():
        if key in policy and algorithm not in present:
            findings.append(_finding(f"MISSING:{algorithm}",algorithm,"INCONCLUSIVE",None,policy[key],"trace contains no evidence for this requested check",reason="trace contains no evidence for this requested check"))
    status="FAIL" if any(f["status"]=="FAIL" for f in findings) else ("INCONCLUSIVE" if any(f["status"]=="INCONCLUSIVE" for f in findings) else "PASS")
    return {"schema_version":"0.5","asset_type":"game_trace","status":status,"completeness":"COMPLETE" if findings and all(f["status"] in {"PASS","FAIL"} for f in findings) else "INCOMPLETE",
            "engine":trace.engine,"engine_version":trace.engine_version,"timebase":trace.timebase,"random_seed":trace.random_seed,"results":findings}
