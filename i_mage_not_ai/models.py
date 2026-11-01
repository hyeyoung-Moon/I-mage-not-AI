"""Typed, versioned manifest and evidence contracts."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Origin = Literal["user_spec", "project_config", "source_native", "agent_inferred"]

class Provenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    origin: Origin

class AssetRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str
    id: str | None = None
    format: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

class FeatureGeometry(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

class Feature(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1)
    role: str
    parent_id: str | None = None
    geometry: FeatureGeometry | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance | None = None

class FeatureInventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: Literal["approved", "not_found", "not_applicable", "unreviewed"]
    feature_id: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str | None = None

class FeatureManifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["0.1", "0.5", "0.6"]
    asset: AssetRef
    features: list[Feature]
    intent: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    overlap_constraints: list[dict[str, Any]] = Field(default_factory=list)
    feature_inventory: list[FeatureInventoryItem] | None = None

    @model_validator(mode="after")
    def validate_feature_relations(self):
        if self.schema_version in {"0.5", "0.6"} and not self.asset.sha256:
            raise ValueError(f"schema_version {self.schema_version} requires asset.sha256")
        if self.schema_version == "0.6" and not self.feature_inventory:
            raise ValueError("schema_version 0.6 requires a non-empty feature_inventory")
        ids=[feature.id for feature in self.features]
        if len(ids)!=len(set(ids)): raise ValueError("feature IDs must be unique")
        known=set(ids)
        if self.feature_inventory is not None:
            inventory_ids=[item.id for item in self.feature_inventory]
            if len(inventory_ids)!=len(set(inventory_ids)):
                raise ValueError("feature_inventory IDs must be unique")
            for item in self.feature_inventory:
                if item.status == "approved" and item.feature_id not in known:
                    raise ValueError(f"feature inventory {item.id}: approved entry must reference a known feature_id")
                if item.status != "approved" and item.feature_id is not None:
                    raise ValueError(f"feature inventory {item.id}: feature_id is only valid for approved entries")
                if item.status == "not_applicable" and not (item.rationale or "").strip():
                    raise ValueError(f"feature inventory {item.id}: not_applicable requires a rationale")
                candidate_ids: list[str] = []
                for candidate in item.candidates:
                    if set(candidate) != {"candidate_id", "status"} or not isinstance(candidate.get("candidate_id"), str) or not candidate["candidate_id"].strip() or candidate.get("status") not in {"approved", "rejected", "unreviewed"}:
                        raise ValueError(f"feature inventory {item.id}: each candidate needs candidate_id and approved/rejected/unreviewed status")
                    candidate_ids.append(candidate["candidate_id"])
                if len(candidate_ids) != len(set(candidate_ids)):
                    raise ValueError(f"feature inventory {item.id}: candidate IDs must be unique")
                if item.status == "approved" and item.candidates and not any(candidate["status"] == "approved" for candidate in item.candidates):
                    raise ValueError(f"feature inventory {item.id}: approved feature must have at least one approved candidate when candidates are listed")
                if item.status == "not_found" and any(candidate["status"] == "approved" for candidate in item.candidates):
                    raise ValueError(f"feature inventory {item.id}: not_found conflicts with an approved candidate")
            approved_features=[item.feature_id for item in self.feature_inventory if item.status == "approved"]
            if len(approved_features) != len(set(approved_features)) or set(approved_features) != known:
                raise ValueError("every manifest feature must be represented by exactly one approved feature_inventory entry")
        for feature in self.features:
            if feature.parent_id is not None and feature.parent_id not in known:
                raise ValueError(f"feature {feature.id}: unknown parent_id {feature.parent_id}")
        return self

class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: str | None = None
    source_sha256: str
    data_type: str
    coordinate_system: str | None = None
    color_space: str | None = None
    unit: str | None = None
    provenance: Origin
    adapter_id: str
    adapter_version: str

class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule_id: str
    feature_id: str | None = None
    status: Literal["PASS", "FAIL", "INCONCLUSIVE", "SKIPPED"]
    algorithm: str
    measured: Any
    threshold: Any = None
    formula: str
    region: Any = None

class TemporalSample(BaseModel):
    """Reusable typed temporal evidence record; the executable JSON trace is in game.py."""
    model_config = ConfigDict(extra="forbid")
    frame_index: int | None = None
    timestamp: float
    timebase: str
    coordinate_system: str
    units: str
    transform_hierarchy: list[str] = Field(default_factory=list)
    interpolation_policy: str
    random_seed: int | None = None
    value: Any

class TemporalTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: str
    samples: list[TemporalSample]

class EventTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: str
    events: list[TemporalSample]

class ParticleTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: str
    spawn: TemporalSample
    death: TemporalSample | None = None
