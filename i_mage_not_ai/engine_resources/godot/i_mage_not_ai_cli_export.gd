extends SceneTree
## Explicit CLI exporter. Loading the selected scene can execute project scripts.

func _initialize() -> void:
	call_deferred("_run_export")

func _run_export() -> void:
	var args := OS.get_cmdline_user_args()
	var scene_path := ""
	var output_path := ""
	var index := 0
	while index < args.size():
		if args[index] == "--scene" and index + 1 < args.size():
			scene_path = args[index + 1]
			index += 2
		elif args[index] == "--output" and index + 1 < args.size():
			output_path = args[index + 1]
			index += 2
		else:
			push_error("Unknown or incomplete argument: %s" % args[index])
			quit(2)
			return
	if scene_path.is_empty() or output_path.is_empty():
		push_error("Usage: --scene res://scene.tscn --output /absolute/trace.json")
		quit(2)
		return
	var packed := load(scene_path) as PackedScene
	if packed == null:
		push_error("Cannot load selected PackedScene: %s" % scene_path)
		quit(3)
		return
	var scene_root := packed.instantiate()
	root.add_child(scene_root)
	var players := scene_root.find_children("*", "AnimationPlayer", true, false)
	if scene_root is AnimationPlayer:
		players.push_front(scene_root)
	if players.is_empty():
		push_error("Selected scene has no AnimationPlayer.")
		quit(3)
		return
	var result := _build_trace(players[0])
	var file := FileAccess.open(output_path, FileAccess.WRITE)
	if file == null:
		push_error("Cannot write trace: %s" % output_path)
		quit(4)
		return
	file.store_string(JSON.stringify(result, "\t"))
	file.close()
	print("I_MAGE_NOT_AI_EXPORT_PASS Godot tracks=%d events=%d output=%s" % [result.trace.tracks.size(), result.trace.events.size(), output_path])
	quit(0)

func _build_trace(player: AnimationPlayer) -> Dictionary:
	var tracks: Array[Dictionary] = []
	var events: Array[Dictionary] = []
	for animation_name in player.get_animation_list():
		var animation: Animation = player.get_animation(animation_name)
		var node_channels: Dictionary = {}
		for track_index in animation.get_track_count():
			var track_type := animation.track_get_type(track_index)
			var node_path := String(animation.track_get_path(track_index)).split(":", true, 1)[0]
			if track_type in [Animation.TYPE_POSITION_3D, Animation.TYPE_ROTATION_3D, Animation.TYPE_SCALE_3D]:
				if not node_channels.has(node_path):
					node_channels[node_path] = {"position": -1, "rotation": -1, "scale": -1}
				var channel := "position" if track_type == Animation.TYPE_POSITION_3D else ("rotation" if track_type == Animation.TYPE_ROTATION_3D else "scale")
				node_channels[node_path][channel] = track_index
			elif track_type == Animation.TYPE_METHOD:
				for key_index in animation.track_get_key_count(track_index):
					var event_value: Dictionary = animation.track_get_key_value(track_index, key_index)
					events.append({"id": "%s:event:%d:%d" % [animation_name, track_index, key_index],
						"event_type": str(event_value.get("method", "method")),
						"time": animation.track_get_key_time(track_index, key_index)})
		var track_ids: Dictionary = {}
		for node_path in node_channels:
			track_ids[node_path] = "%s::%s" % [animation_name, node_path]
		for node_path in node_channels:
			var channels: Dictionary = node_channels[node_path]
			if int(channels.rotation) >= 0:
				var rotation_samples: Array[Dictionary] = []
				for key_index in animation.track_get_key_count(int(channels.rotation)):
					var time := animation.track_get_key_time(int(channels.rotation), key_index)
					var q: Quaternion = animation.rotation_track_interpolate(int(channels.rotation), time)
					rotation_samples.append({"time": time, "value": [q.x, q.y, q.z, q.w]})
				tracks.append({"id": "%s::%s::rotation" % [animation_name, node_path], "kind": "rotation",
					"units": "quaternion", "coordinate_system": "godot_local_3d", "samples": rotation_samples})
			if int(channels.position) < 0:
				continue
			var sample_times: Array[float] = []
			for channel in ["position", "rotation", "scale"]:
				var channel_index := int(channels[channel])
				if channel_index >= 0:
					for key_index in animation.track_get_key_count(channel_index):
						var time := animation.track_get_key_time(channel_index, key_index)
						if not sample_times.has(time):
							sample_times.append(time)
			sample_times.sort()
			var samples: Array[Dictionary] = []
			for time in sample_times:
				var position: Vector3 = animation.position_track_interpolate(int(channels.position), time)
				var rotation: Quaternion = animation.rotation_track_interpolate(int(channels.rotation), time) if int(channels.rotation) >= 0 else Quaternion.IDENTITY
				var scale: Vector3 = animation.scale_track_interpolate(int(channels.scale), time) if int(channels.scale) >= 0 else Vector3.ONE
				var basis := Basis(rotation).scaled(scale)
				samples.append({"time": time, "value": [position.x, position.y, position.z],
					"basis_matrix": [[basis.x.x, basis.y.x, basis.z.x], [basis.x.y, basis.y.y, basis.z.y], [basis.x.z, basis.y.z, basis.z.z]]})
			var parent_path := String(node_path).get_base_dir()
			var track_data := {"id": track_ids[node_path], "kind": "position", "units": "engine_unit",
				"coordinate_system": "godot_local_3d", "coordinate_space": "local", "samples": samples}
			if track_ids.has(parent_path):
				track_data["parent_track_id"] = track_ids[parent_path]
			tracks.append(track_data)
	return {"format": "i_mage_not_ai.godot-export/1", "engine_version": Engine.get_version_info().string,
		"trace": {"schema_version": "1", "timebase": "seconds", "tracks": tracks, "events": events}}
