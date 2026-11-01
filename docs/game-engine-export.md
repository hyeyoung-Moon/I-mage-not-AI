# Game engine trace exports

The trace analyzer reads versioned JSON exports and does not open an engine project. A separate, explicitly invoked `i-mage-not-ai project export-trace` command can launch Godot or Unity against a user-selected scene/clip. Both engine wrappers contain the canonical trace defined in [`game-trace.schema.json`](../schemas/game-trace.schema.json); an exporter is responsible for sampling engine objects and writing canonical units, coordinate labels, tracks, events, and particle records.

Godot 4 exports use:

```json
{
  "format": "i_mage_not_ai.godot-export/1",
  "engine_version": "4.3",
  "trace": {
    "schema_version": "1",
    "timebase": "seconds",
    "tracks": [],
    "events": [],
    "particles": []
  }
}
```

Unity exports use `"format": "i_mage_not_ai.unity-export/1"` and the Unity editor version. The `trace` payload follows the same canonical schema. Position tracks may carry local coordinates, a `parent_track_id`, and a static 2D/3D `basis_matrix`; samples may override that matrix to record time-varying rotation and scale. Hierarchy samples must be synchronized, and parent/child units and coordinate labels must match. Rotation tracks use normalized or normalizable `[x, y, z, w]` quaternions. The reader rejects unrecognized wrapper fields and reports the engine/version in review evidence.

The read-only `project preview` command recognizes project markers and inspects bounded text metadata/references; it does not execute importers, scripts, plugins, or editor processes. It is not a complete parser for native `.tscn`, `.tres`, `.unity`, or `.asset` formats. `project export-trace` is a separate execution boundary: the user must supply an explicit project, selection, engine executable, output path, and `--allow-project-code`. Loading a chosen Godot scene or invoking a Unity editor can execute code in that project. Exporters must be versioned and tested against each supported engine release before their output is treated as authoritative.

## Included editor exporters

`engine/godot/i_mage_not_ai_export.gd` is an `EditorScript`: open the target scene in Godot, select **File → Run** for the script, and it writes `res://i_mage_not_ai-godot-export.json` (or the first command-line user argument). It reads keyed 3D position/rotation/scale tracks and method-event timestamps from the first `AnimationPlayer`; it does not instantiate scenes, advance animation, or invoke method tracks.

`engine/unity/Editor/i_mage_not_aiTraceExporter.cs` adds **Assets → Export AnimationClip as i_mage_not_ai Trace**. Select an `AnimationClip` and choose the output file. It reads `AnimationUtility` transform curves and event metadata without instantiating a GameObject or invoking animation events.

The CLI exporter uses the same Godot script and Unity C# exporter:

```sh
# Godot selection is a project-relative .tscn path.
i-mage-not-ai project export-trace --engine godot --project <project-root> \
  --engine-executable <godot-console-executable> --selection scenes/hero.tscn \
  --output <absolute-trace.json> --allow-project-code

# Install the Unity Editor extension once, then select an existing .anim under Assets.
i-mage-not-ai project install-exporter --engine unity --project <project-root>
i-mage-not-ai project export-trace --engine unity --project <project-root> \
  --engine-executable <Unity-executable> --selection Assets/Animations/hero.anim \
  --output <absolute-trace.json> --allow-project-code
```

Unity exporter installation is explicit and refuses to overwrite an existing file. Scanning and exporter installation do not launch the project. The engine invocation can run project/editor code; use it only for projects you trust. Output files are validated against the shared trace schema before the command reports success.

## Runtime evidence

`i-mage-not-ai game runtime-check --evidence <runtime-evidence.json> --policy <runtime-policy.json>` analyzes an engine-produced `i_mage_not_ai.runtime-evidence/1` record. It compares observed callback invocation counts/times and particle sample times, active counts (when exposed), and live particle bounds with explicit numeric policy limits. The checker does not execute engine code and ignores engine-authored `checks` booleans; it recomputes each finding from the observation arrays.

The Godot 4.6.3 fixture runs a scene under Vulkan Forward+ at fixed 60 FPS, advances an `AnimationPlayer` method track to invoke the callback, and samples `GPUParticles2D.capture_rect()` with a fixed seed. A dummy/headless renderer does not provide the GPU particle buffer needed for this measurement, so this check must run with a renderer. Godot exposes the current particle AABB here, not a live particle count or each particle's state.

The Unity 6000.3.25f1 fixture enters Play Mode, plays an `Animator` clip containing an `AnimationEvent`, and simulates a seeded `ParticleSystem`; it records active particles through `GetParticles()` and measures their positions. These fixtures demonstrate actual engine observations for the included deterministic scenarios. Arbitrary projects still need a callback instrumentation component or project-specific adapter and an explicit scenario that triggers the relevant emitters. A fixed seed does not guarantee bit-identical GPU pixels across different renderer/driver versions; this runtime check measures declared engine state, not visual semantics.

Example for the Godot fixture:

```powershell
& 'E:\Godot\4.6.3\Godot_v4.6.3-stable_win64_console.exe' --path integration-fixtures/godot --scene res://runtime_scene.tscn --fixed-fps 60 --rendering-method forward_plus
.venv\Scripts\python.exe -m i_mage_not_ai game runtime-check --evidence integration-fixtures/godot/runtime-evidence.json --policy integration-fixtures/godot/runtime-policy.json
```

Runtime evidence and review reports are separate from static animation exports, preserving the distinction between declared events and events actually observed during playback.

## Rendered frame evidence

Numeric runtime observations and rendered output are separate gates. Capture actual runtime PNG frames for the named scene/scenario at a declared target resolution. Store them beside a bundle using `i_mage_not_ai.render-evidence/1`:

```json
{
  "format": "i_mage_not_ai.render-evidence/1",
  "capture_source": "engine_runtime",
  "engine": "godot",
  "engine_version": "4.6.3",
  "platform": "android",
  "scenario_id": "hero-attack-v1",
  "timebase": "seconds",
  "resolution": [720, 1280],
  "import_settings": {"filter": "linear", "mipmaps": true, "compression": "lossless"},
  "captures": [
    {"frame_index": 0, "timestamp": 0.0, "image": "frames/000.png", "event_ids": []},
    {"frame_index": 1, "timestamp": 0.1, "image": "frames/001.png", "event_ids": ["attack-start"]}
  ]
}
```

Run `i-mage-not-ai game render-check --evidence <bundle.json> --policy <render-policy.json> --output <report.json>`. The CLI recomputes frame hashes, enforces exact declared dimensions, measures the maximum adjacent-frame changed-pixel ratio using an explicit RGB delta threshold, and checks uniquely annotated event captures against expected timestamps and tolerances. Optional `flash` policy measures flash event count/rate, affected screen area, duration, and red-dominant changed area from configured thresholds. Missing frames or ambiguous event annotations remain INCONCLUSIVE. This checker does not launch the engine, verify the honesty of the capture adapter, or decide whether a visual change is artistically intended. Capture multiple actual target sizes/devices when those are in scope; one resolution does not establish mobile or camera-distance readability generally.

To require event-centered context, add `before_frames` and `after_frames` to a declared event policy. Every engine frame is saved; the checker verifies the requested number of saved frames on both sides of the uniquely tagged event. If the event occurs too close to the start or end of the capture, the event finding is INCONCLUSIVE and the suite cannot pass.

`game capture-suite` automates a matrix of resolutions and camera settings, then runs `render-check` and the integrated `game gate` for each case. The supplied trace and runtime reports must already be COMPLETE and use the suite's scenario ID. Each case may override the selected scene and camera node path. For Godot, select a `Camera3D` to use `camera_distance_offset` along local +Z, or a `Camera2D` to use `camera_zoom_scale`. Unity applies distance offset along the camera axis, where a positive value moves away from the subject, and applies zoom scale to orthographic size or perspective field of view. A distance change on an orthographic camera does not change apparent size; use zoom scale for that case. This does not launch a physical phone or reproduce mobile OS/display behavior.

```json
{
  "cases": [
    {"id": "portrait-near", "width": 720, "height": 1280,
     "camera_node_path": "Player/Camera3D", "camera_distance_offset": -1.0},
    {"id": "portrait-far", "width": 720, "height": 1280,
     "camera_node_path": "Player/Camera3D", "camera_distance_offset": 2.0},
    {"id": "landscape-far", "width": 1280, "height": 720,
     "camera_node_path": "Player/Camera2D", "camera_zoom_scale": 0.8}
  ]
}
```

```powershell
i-mage-not-ai game capture-suite --engine godot --project <project-root> `
  --engine-executable <Godot-executable> --scene scenes/hero.tscn `
  --scenario-id hero-attack-v1 --suite <capture-suite.json> `
  --event-map <godot-events.json> --render-policy <render-policy.json> `
  --review-report <trace-review.json> --runtime-report <runtime-report.json> `
  --frames 120 --fps 60 --output <new-suite-report.json> --allow-project-code
```

Each case creates its evidence bundle, render-check report, and game-gate report under a new sibling directory named after the suite report. Suite status is FAIL if any case fails, PASS only if every case passes completely, and otherwise INCONCLUSIVE. Unity uses a named scene `Camera` path instead of a Godot node path; the adapter captures that camera to a RenderTexture, so Screen Space Overlay UI remains outside its image.

The capture exporter must preserve the engine build/version, platform, scenario ID, resolution, and effective import settings in the bundle. Include the same `scenario_id` in the v0.6 game-trace manifest and runtime evidence to bind the three reports. Aggregate with `i-mage-not-ai game gate --review-report <trace-review.json> --runtime-report <runtime-report.json> --render-report <render-report.json>`. The gate requires all three reports to be PASS/COMPLETE, of the expected report type, and tied to the same scenario; otherwise the combined status is INCONCLUSIVE or FAIL. Engine state traces may be joined to this evidence by scenario and event IDs, but a state callback alone does not prove its effect appeared in rendered pixels.

## Running the capture adapters

The CLI runs the selected engine project. It requires `--allow-project-code`; loading the selected scene and its scripts can execute project code. Use an engine executable whose renderer is available. Do not pass Godot `--headless` or Unity `-nographics` for rendered capture.

Godot uses the bundled external runner and does not modify the project:

```powershell
i-mage-not-ai game capture --engine godot --project <project-root> `
  --engine-executable <Godot-executable> --scene scenes/hero.tscn `
  --scenario-id hero-attack-v1 --width 720 --height 1280 `
  --frames 120 --fps 60 --event-map <godot-events.json> `
  --output <new-evidence.json> --allow-project-code
```

The optional Godot event map binds observed project signals to event IDs. To require three saved frames before and after the event, declare the matching window in `render-policy.json`:

```json
{"events":[{"id":"attack-start","node_path":"Player/Weapon","signal":"attack_started"}]}
```

```json
{"event_times":{"attack-start":{"time":0.5,"tolerance":0.05,"before_frames":3,"after_frames":3}}}
```

Unity needs the capture adapter copied into `Assets/` once. Installation writes two source files and does not start Unity:

```powershell
i-mage-not-ai project install-capture-adapter --engine unity --project <project-root>
i-mage-not-ai game capture --engine unity --project <project-root> `
  --engine-executable <Unity-editor-executable> --scene Assets/Scenes/Hero.unity `
  --scenario-id hero-attack-v1 --width 720 --height 1280 `
  --frames 120 --fps 60 --output <new-evidence.json> --allow-project-code
```

Unity records frames from the selected scene's `Camera.main` RenderTexture unless `--camera-node-path` selects another camera. A game can call `i_mage_not_aiRenderCapture.MarkEvent("attack-start")` from its actual event handler to associate the next captured frame with that event. This Unity mode captures the selected camera image; it does not composite Screen Space Overlay canvases or multiple displays. Use a project-specific camera/compositor adapter when those are required. Both engines require a new output JSON path and a new companion frame directory for each capture run.
