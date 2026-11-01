@tool
extends EditorScript
## Export keyframed Node3D transforms and method-track timestamps without playing animations.

func _run() -> void:
	var scene_root: Node = EditorInterface.get_edited_scene_root()
	if scene_root == null:
		push_error("Open a scene before running the i_mage_not_ai exporter.")
		return
	var players := scene_root.find_children("*", "AnimationPlayer", true, false)
	if players.is_empty():
		push_error("The open scene has no AnimationPlayer.")
		return
	var player: AnimationPlayer = players[0]
	var animations: PackedStringArray = player.get_animation_list()
	var tracks: Array[Dictionary] = []
	var events: Array[Dictionary] = []
	for animation_name in animations:
		var animation: Animation = player.get_animation(animation_name)
		if animation == null:
			continue
		var node_tracks: Dictionary = {}
		for track_index in animation.get_track_count():
			var track_type := animation.track_get_type(track_index)
			var track_path := animation.track_get_path(track_index)
			var node_path := String(track_path).split(":", true, 1)[0]
			if track_type in [Animation.TYPE_POSITION_3D, Animation.TYPE_ROTATION_3D, Animation.TYPE_SCALE_3D]:
				if not node_tracks.has(node_path):
					node_tracks[node_path] = {"position": -1, "rotation": -1, "scale": -1}
				var key := "position" if track_type == Animation.TYPE_POSITION_3D else ("rotation" if track_type == Animation.TYPE_ROTATION_3D else "scale")
				node_tracks[node_path][key] = track_index
			elif track_type == Animation.TYPE_METHOD:
				for key_index in animation.track_get_key_count(track_index):
					var event_value: Dictionary = animation.track_get_key_value(track_index, key_index)
					events.append({
						"id": "%s:event:%d:%d" % [animation_name, track_index, key_index],
						"event_type": str(event_value.get("method", "method")),
						"time": animation.track_get_key_time(track_index, key_index)
					})
		var track_ids: Dictionary = {}
		for node_path in node_tracks:
			track_ids[node_path] = "%s::%s" % [animation_name, node_path]
		for node_path in node_tracks:
			var indices: Dictionary = node_tracks[node_path]
			if int(indices.rotation) >= 0:
				var rotation_samples: Array[Dictionary] = []
				for rotation_key in animation.track_get_key_count(int(indices.rotation)):
					var rotation_time := animation.track_get_key_time(int(indices.rotation), rotation_key)
					var quaternion: Quaternion = animation.rotation_track_interpolate(int(indices.rotation), rotation_time)
					rotation_samples.append({"time": rotation_time, "value": [quaternion.x, quaternion.y, quaternion.z, quaternion.w]})
				tracks.append({
					"id": "%s::%s::rotation" % [animation_name, node_path],
					"kind": "rotation", "units": "quaternion", "coordinate_system": "godot_local_3d",
					"samples": rotation_samples
				})
			if int(indices.position) < 0:
				continue
			var sample_times: Array[float] = []
			for channel in ["position", "rotation", "scale"]:
				var channel_index := int(indices[channel])
				if channel_index >= 0:
					for key_index in animation.track_get_key_count(channel_index):
						var key_time := animation.track_get_key_time(channel_index, key_index)
						if not sample_times.has(key_time):
							sample_times.append(key_time)
			sample_times.sort()
			var samples: Array[Dictionary] = []
			for sample_time in sample_times:
				var position: Vector3 = animation.position_track_interpolate(int(indices.position), sample_time)
				var rotation := Quaternion.IDENTITY
				var scale := Vector3.ONE
				if int(indices.rotation) >= 0:
					rotation = animation.rotation_track_interpolate(int(indices.rotation), sample_time)
				if int(indices.scale) >= 0:
					scale = animation.scale_track_interpolate(int(indices.scale), sample_time)
				var basis := Basis(rotation).scaled(scale)
				samples.append({
					"time": sample_time,
					"value": [position.x, position.y, position.z],
					"basis_matrix": [
						[basis.x.x, basis.y.x, basis.z.x],
						[basis.x.y, basis.y.y, basis.z.y],
						[basis.x.z, basis.y.z, basis.z.z]
					]
				})
			var parent_path: String = String(node_path).get_base_dir()
			var parent_id: Variant = track_ids.get(parent_path, null)
			var track_data := {
				"id": track_ids[node_path], "kind": "position", "units": "engine_unit",
				"coordinate_system": "godot_local_3d", "coordinate_space": "local",
				"samples": samples
			}
			if parent_id != null:
				track_data["parent_track_id"] = parent_id
			tracks.append(track_data)
	var trace := {"schema_version": "1", "timebase": "seconds", "tracks": tracks, "events": events}
	var export_doc := {
		"format": "i_mage_not_ai.godot-export/1",
		"engine_version": Engine.get_version_info().string,
		"trace": trace
	}
	var output_path := "res://i_mage_not_ai-godot-export.json"
	var user_args := OS.get_cmdline_user_args()
	if not user_args.is_empty():
		output_path = user_args[0]
	var output := FileAccess.open(output_path, FileAccess.WRITE)
	if output == null:
		push_error("Cannot write export file: %s" % output_path)
		return
	output.store_string(JSON.stringify(export_doc, "\t"))
	output.close()
	print("i_mage_not_ai exported %d transform tracks and %d method events to %s" % [tracks.size(), events.size(), output_path])
