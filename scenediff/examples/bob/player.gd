extends CharacterBody3D

@export var speed: float = 7.0
@export var sprint_multiplier: float = 1.5

func _physics_process(_delta: float) -> void:
	var direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	var current_speed := speed
	if Input.is_action_pressed("ui_accept"):
		current_speed *= sprint_multiplier
	velocity = Vector3(direction.x, 0, direction.y) * current_speed
	move_and_slide()
