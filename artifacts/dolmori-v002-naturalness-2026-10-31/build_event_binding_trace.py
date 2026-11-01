#!/usr/bin/env python3
"""Append isolated Unity PAC ability/VFX observations to a canonical game trace."""
from __future__ import annotations

import json
from collections import Counter
from statistics import median
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build():
    base = read(ROOT / "unity-game-trace.json")
    runtime = read(ROOT / "unity-event-binding-runtime.json")
    if runtime.get("observedProjectiles") != runtime.get("expectedTotalEffects"):
        raise SystemExit("event-binding runtime did not observe the complete expected effect set")
    if not all(row.get("finished") and row.get("positionSamples") for row in runtime["spawns"]):
        raise SystemExit("event-binding runtime lacks a completed effect lifecycle/position trace")

    trace = json.loads(json.dumps(base))
    base_time = float(runtime["eventStartSeconds"])
    new_events = []
    new_particles = []
    new_tracks = []
    expected_times = {}
    for item in runtime["spawns"]:
        event_id = item["id"]
        observed = float(item["observedTimeSeconds"])
        expected = float(item["expectedTimeSeconds"])
        event = {"id": event_id, "event_type": "PAC_ABILITY_ERUPTION_SPRITE_EFFECT", "time": observed,
                 "frame_index": int(item["bodyFrameAtTrigger"])}
        new_events.append(event)
        expected_times[event_id] = expected

        track_id = f"position-{event_id}"
        samples = [{"time": base_time + float(point["timeSeconds"]), "value": [float(point["x"]), float(point["y"])]}
                   for point in item["positionSamples"]]
        new_tracks.append({"id": track_id, "kind": "position", "units": "ui_pixel",
                           "coordinate_system": "unity_canvas_world", "interpolation": "runtime_sampled",
                           "samples": samples})
        new_particles.append({"id": event_id, "spawn_time": observed,
                              "death_time": float(item["observedDeathTimeSeconds"]),
                              "position_track_id": track_id})

    trace["events"] = list(trace.get("events", [])) + new_events
    trace["particles"] = list(trace.get("particles", [])) + new_particles
    trace["tracks"] = list(trace.get("tracks", [])) + new_tracks
    policy = {"expected_event_times": expected_times, "max_event_timing_error": None,
              "max_particle_lifetime": None}
    write(ROOT / "unity-event-binding-trace.json", trace)
    write(ROOT / "event-binding-measurement-policy.json", policy)
    receipt = read(ROOT / "unity-event-binding-receipt.json")
    burst = [item for item in runtime["spawns"] if int(item["direction"]) == 0]
    event_errors = [float(item["timingErrorSeconds"]) for item in runtime["spawns"]]
    lifetimes = [float(item["observedDeathTimeSeconds"]) - float(item["observedTimeSeconds"]) for item in runtime["spawns"]]
    position_errors = [float(item["positionErrorPx"]) for item in runtime["spawns"]]
    position_samples = sum(len(item["positionSamples"]) for item in runtime["spawns"])
    summary = {
        "scope": runtime.get("scope"), "unity_version": receipt.get("unity"),
        "unity_receipt_passed": receipt.get("passed"), "unity_check_count": len(receipt.get("checks", [])),
        "unity_runtime_error_count": len(receipt.get("runtimeErrors", [])),
        "event_probe_checks": runtime.get("checks", []),
        "candidate_keyframe": {"action": "Ability", "animation": "RearUp", "logical_frame": runtime["candidateAbilityFrame"],
                               "candidate_time_seconds": 10 / 36, "directions_observed": len(runtime.get("bodyEvents", [])),
                               "directions_on_candidate_frame": sum(bool(item["firedOnCandidateFrame"]) for item in runtime.get("bodyEvents", []))},
        "synthetic_effect_route": {"skill": "ERUPTION", "expected_effects": runtime["expectedTotalEffects"],
                                    "observed_effects": runtime["observedProjectiles"], "planned_burst_effects": len(burst),
                                    "burst_body_frame_counts": dict(sorted(Counter(int(item["bodyFrameAtTrigger"]) for item in burst).items())),
                                    "all_effects_finished": all(bool(item["finished"]) for item in runtime["spawns"]),
                                    "active_effects_at_end": runtime["activeEffectsAtEnd"],
                                    "unsupported_skills": runtime["unsupportedSkills"], "missing_visual_assets": runtime["missingVisualAssets"]},
        "measurements": {"event_timing_error_seconds": {"median": median(event_errors), "max": max(event_errors)},
                         "effect_lifetime_seconds": {"min": min(lifetimes), "median": median(lifetimes), "max": max(lifetimes)},
                         "max_board_origin_position_error_ui_pixels": max(position_errors),
                         "position_track_count": len(runtime["spawns"]), "position_sample_count": position_samples},
        "cli": None,
        "limits": ["Synthetic events were injected after observing the candidate body frame; production battle-event binding is not proved.",
                   "PAC ERUPTION is a sprite effect route, not a Unity ParticleSystem.",
                   "Unity used Null graphics; rendered pixels and visual appearance are not verified.",
                   "No project thresholds were supplied; CLI findings are measurement-only INCONCLUSIVE."],
    }
    analysis_path = ROOT / "event-binding-game-analysis.json"
    if analysis_path.exists():
        analysis = read(analysis_path)
        summary["cli"] = {"status": analysis.get("status"), "finding_count": len(analysis.get("results", [])),
                          "status_counts": dict(sorted(Counter(str(item.get("status")) for item in analysis.get("results", [])).items())),
                          "event_finding_count": sum(str(item.get("id", "")).startswith("EVENT:") for item in analysis.get("results", [])),
                          "particle_lifetime_finding_count": sum(str(item.get("id", "")).startswith("PARTICLE:") and ":BOUNDS" not in str(item.get("id", "")) for item in analysis.get("results", []))}
    write(ROOT / "event-binding-summary.json", summary)
    print(json.dumps({"trace": "unity-event-binding-trace.json", "policy": "event-binding-measurement-policy.json",
                      "events": len(new_events), "position_tracks": len(new_tracks),
                      "completed_lifecycles": len(new_particles), "thresholds": "measurement only; no pass/fail limits invented"}, indent=2))


if __name__ == "__main__":
    build()
