extends CharacterBody3D

@export var speed: float = 5.0

func _physics_process(_delta: float) -> void:
	var direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	velocity = Vector3(direction.x, 0, direction.y) * speed
	move_and_slide()
