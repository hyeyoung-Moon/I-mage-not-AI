extends SceneTree
## Rendered-frame capture runner. Loading the selected scene executes project scripts.

var _pending_events: Array[String] = []

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var args := _parse_args(OS.get_cmdline_user_args())
	if args.is_empty():
		quit(2)
		return
	var scene_path: String = args.scene
	var output_path: String = args.output
	var scenario_id: String = args.scenario_id
	var frame_count: int = args.frames
	var fps: int = args.fps
	var resolution := Vector2i(args.width, args.height)
	var event_map: Array = args.events
	var camera_node_path: String = args.camera_node_path
	var camera_distance_offset: float = args.camera_distance_offset
	var camera_zoom_scale: float = args.camera_zoom_scale
	var selected_camera: Node = null
	if DisplayServer.get_name() == "headless":
		push_error("Rendered capture requires an active renderer; headless mode cannot provide engine pixels.")
		quit(3)
		return
	Engine.max_fps = fps
	Engine.physics_ticks_per_second = fps
	get_root().size = resolution
	var packed := load(scene_path) as PackedScene
	if packed == null:
		push_error("Cannot load selected scene: %s" % scene_path)
		quit(3)
		return
	var scene_root := packed.instantiate()
	if not camera_node_path.is_empty():
		selected_camera = scene_root.get_node_or_null(NodePath(camera_node_path))
		if selected_camera is Camera3D:
			pass
		elif selected_camera is Camera2D:
			if not is_zero_approx(camera_distance_offset):
				push_error("camera-distance-offset is only valid for Camera3D; use camera-zoom-scale for Camera2D")
				quit(3)
				return
			selected_camera.zoom *= camera_zoom_scale
		else:
			push_error("camera-node-path must select a Camera2D or Camera3D inside the scene: %s" % camera_node_path)
			quit(3)
			return
	elif not is_zero_approx(camera_distance_offset) or not is_equal_approx(camera_zoom_scale, 1.0):
		push_error("camera adjustments require camera-node-path")
		quit(3)
		return
	var hook_error := _connect_event_hooks(scene_root, event_map)
	if not hook_error.is_empty():
		push_error(hook_error)
		quit(3)
		return
	get_root().add_child(scene_root)
	# Give project _ready handlers, camera setup, and the compositor time to settle.
	for _index in range(2):
		await process_frame
		await RenderingServer.frame_post_draw
	if selected_camera is Camera3D:
		selected_camera.translate_object_local(Vector3(0.0, 0.0, camera_distance_offset))
		selected_camera.fov = clampf(selected_camera.fov / camera_zoom_scale, 1.0, 179.0)
	elif selected_camera is Camera2D:
		selected_camera.zoom *= camera_zoom_scale
	await process_frame
	await RenderingServer.frame_post_draw
	var output_directory := output_path.get_base_dir()
	var stem := output_path.get_file().get_basename()
	var frames_directory := output_directory.path_join(stem + "_frames")
	var directory_error := DirAccess.make_dir_recursive_absolute(frames_directory)
	if directory_error != OK:
		push_error("Cannot create capture frame directory: %s" % frames_directory)
		quit(4)
		return
	var captures: Array[Dictionary] = []
	var capture_clock_start := Time.get_ticks_usec()
	for frame_index in range(frame_count):
		await process_frame
		await RenderingServer.frame_post_draw
		var image := get_root().get_texture().get_image()
		var capture_timestamp := float(Time.get_ticks_usec() - capture_clock_start) / 1000000.0
		if image == null or image.get_size() != resolution:
			push_error("Rendered viewport size mismatch; expected %s, received %s" % [resolution, image.get_size() if image else Vector2i.ZERO])
			quit(4)
			return
		var image_name := "%06d.png" % frame_index
		var image_path := frames_directory.path_join(image_name)
		var save_error := image.save_png(image_path)
		if save_error != OK:
			push_error("Cannot save rendered capture: %s" % image_path)
			quit(4)
			return
		var relative_image := stem + "_frames/" + image_name
		captures.append({"frame_index": frame_index, "timestamp": capture_timestamp,
			"image": relative_image, "event_ids": _drain_events()})
	var evidence := {
		"format": "i_mage_not_ai.render-evidence/1",
		"capture_source": "engine_runtime",
		"engine": "godot",
		"engine_version": Engine.get_version_info().string,
		"platform": OS.get_name(),
		"scenario_id": scenario_id,
		"timebase": "monotonic_elapsed_seconds",
		"resolution": [resolution.x, resolution.y],
		"import_settings": {
			"capture_fps": fps,
			"renderer": ProjectSettings.get_setting("rendering/renderer/rendering_method", "unknown"),
			"default_texture_filter": ProjectSettings.get_setting("rendering/textures/default_filters/use_nearest_mipmap_filter", "project_default"),
			"window_mode": "runtime_capture",
			"camera_node_path": camera_node_path,
			"camera_distance_offset": camera_distance_offset,
			"camera_zoom_scale": camera_zoom_scale,
			"camera_effective": _camera_state(selected_camera)
		},
		"captures": captures
	}
	var file := FileAccess.open(output_path, FileAccess.WRITE)
	if file == null:
		push_error("Cannot write capture evidence: %s" % output_path)
		quit(4)
		return
	file.store_string(JSON.stringify(evidence, "\t"))
	file.close()
	print("I_MAGE_NOT_AI_RENDER_CAPTURE_PASS Godot frames=%d resolution=%s output=%s" % [frame_count, resolution, output_path])
	quit(0)

func _camera_state(camera: Node) -> Dictionary:
	if camera is Camera3D:
		return {"type": "Camera3D", "position": [camera.global_position.x, camera.global_position.y, camera.global_position.z], "fov_degrees": camera.fov}
	if camera is Camera2D:
		return {"type": "Camera2D", "position": [camera.global_position.x, camera.global_position.y], "zoom": [camera.zoom.x, camera.zoom.y]}
	return {"type": "default_camera"}

func _parse_args(values: PackedStringArray) -> Dictionary:
	var parsed := {"scene": "", "scenario_id": "", "output": "", "frames": 0,
		"fps": 0, "width": 0, "height": 0, "event_map": "", "events": [],
		"camera_node_path": "", "camera_distance_offset": 0.0, "camera_zoom_scale": 1.0}
	var index := 0
	while index < values.size():
		if index + 1 >= values.size():
			push_error("Missing value for capture argument: %s" % values[index])
			return {}
		var key := values[index]
		var value := values[index + 1]
		match key:
			"--scene": parsed.scene = value
			"--scenario-id": parsed.scenario_id = value
			"--output": parsed.output = value
			"--frames": parsed.frames = int(value)
			"--fps": parsed.fps = int(value)
			"--width": parsed.width = int(value)
			"--height": parsed.height = int(value)
			"--event-map": parsed.event_map = value
			"--camera-node-path": parsed.camera_node_path = value
			"--camera-distance-offset": parsed.camera_distance_offset = float(value)
			"--camera-zoom-scale": parsed.camera_zoom_scale = float(value)
			_:
				push_error("Unknown capture argument: %s" % key)
				return {}
		index += 2
	if parsed.scene.is_empty() or parsed.scenario_id.is_empty() or parsed.output.is_empty() or parsed.frames < 2 or parsed.fps < 1 or parsed.width < 1 or parsed.height < 1:
		push_error("Capture requires scene, scenario ID, output, >=2 frames, FPS, width, and height")
		return {}
	if not parsed.event_map.is_empty():
		var event_file := FileAccess.open(parsed.event_map, FileAccess.READ)
		if event_file == null:
			push_error("Cannot read event map: %s" % parsed.event_map)
			return {}
		var data = JSON.parse_string(event_file.get_as_text())
		if not data is Dictionary or data.size() != 1 or not data.has("events") or not data.events is Array:
			push_error("Event map must be a JSON object containing only an events array")
			return {}
		parsed.events = data.events
	return parsed

func _connect_event_hooks(scene_root: Node, events: Array) -> String:
	var seen_ids: Dictionary = {}
	for event in events:
		if not event is Dictionary or event.size() != 3 or not event.has("id") or not event.has("node_path") or not event.has("signal"):
			return "Each event hook requires id, node_path, and signal"
		var event_id := str(event.id)
		if event_id.is_empty() or seen_ids.has(event_id):
			return "Event hook IDs must be non-empty and unique"
		seen_ids[event_id] = true
		var target := scene_root.get_node_or_null(NodePath(str(event.node_path)))
		if target == null:
			return "Event hook node not found: %s" % event.node_path
		var signal_name := StringName(str(event.signal))
		if not target.has_signal(signal_name):
			return "Event hook signal not found: %s.%s" % [event.node_path, event.signal]
		var signal_info: Dictionary = target.get_signal_list().filter(func(item: Dictionary) -> bool: return item.name == signal_name)[0]
		var callback := Callable(self, "_mark_event").unbind(signal_info.args.size()).bind(event_id)
		var error := target.connect(signal_name, callback, CONNECT_DEFERRED)
		if error != OK:
			return "Could not connect event hook %s: %s" % [event_id, error]
	return ""

func _mark_event(event_id: String) -> void:
	_pending_events.append(event_id)

func _drain_events() -> Array[String]:
	var found := _pending_events.duplicate()
	_pending_events.clear()
	return found
