extends Control

signal attack_started

func _ready() -> void:
	var background := ColorRect.new()
	background.color = Color("#26334a")
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(background)

	var actor := ColorRect.new()
	actor.name = "Actor"
	actor.color = Color("#f2bd55")
	actor.size = Vector2(44, 72)
	actor.position = Vector2(20, 54)
	add_child(actor)

	var floor_line := ColorRect.new()
	floor_line.color = Color("#a8d9c0")
	floor_line.position = Vector2(0, 150)
	floor_line.size = Vector2(320, 4)
	add_child(floor_line)

	var movement := create_tween().set_loops()
	movement.tween_property(actor, "position:x", 240.0, 0.8)
	movement.tween_property(actor, "position:x", 20.0, 0.8)
	call_deferred("_emit_attack_started")

func _emit_attack_started() -> void:
	await get_tree().create_timer(0.3).timeout
	attack_started.emit()
