extends CharacterBody3D

@export var patrol_speed: float = 2.0
@export var health: int = 30

func _physics_process(_delta: float) -> void:
	velocity = Vector3.RIGHT * patrol_speed
	move_and_slide()
