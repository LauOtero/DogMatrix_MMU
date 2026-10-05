"""Pruebas de la gestion de corriente de motores (stepper_current)."""

from __future__ import annotations

from dog_matrix.stepper_current import StepperCurrentManager, build_stepper_current


def test_register_and_get_current():
    manager = StepperCurrentManager()
    manager.register_motor("gear", stepper="stepper_mmu_gear", run_current=0.6, hold_current=0.3)
    assert manager.get_current("gear") == {"run_current": 0.6, "hold_current": 0.3}


def test_set_current_updates_and_emits_command():
    emitted = []
    manager = StepperCurrentManager(emit=emitted.append)
    manager.register_motor("gate", stepper="stepper_mmu_0", run_current=0.5, hold_current=0.2)
    assert manager.set_current("gate", run_current=0.8, hold_current=0.4) is True
    assert manager.get_current("gate") == {"run_current": 0.8, "hold_current": 0.4}
    assert len(emitted) == 1
    assert emitted[0] == "SET_TMC_CURRENT STEPPER=stepper_mmu_0 CURRENT=0.8 HOLDCURRENT=0.4"


def test_set_current_partial_update_keeps_other_value():
    emitted = []
    manager = StepperCurrentManager(emit=emitted.append)
    manager.register_motor("gate", stepper="stepper_mmu_0", run_current=0.5, hold_current=0.2)
    manager.set_current("gate", run_current=0.9)
    assert manager.get_current("gate") == {"run_current": 0.9, "hold_current": 0.2}


def test_set_current_unknown_motor_returns_false():
    manager = StepperCurrentManager()
    assert manager.set_current("missing", run_current=1.0) is False


def test_set_current_without_emit_still_updates():
    manager = StepperCurrentManager()
    manager.register_motor("gate", stepper="stepper_mmu_0", run_current=0.5)
    assert manager.set_current("gate", run_current=0.7) is True
    assert manager.get_current("gate")["run_current"] == 0.7


def test_set_current_without_stepper_does_not_emit():
    emitted = []
    manager = StepperCurrentManager(emit=emitted.append)
    manager.register_motor("gate", run_current=0.5)
    manager.set_current("gate", run_current=0.7)
    assert emitted == []


def test_emit_exception_is_isolated():
    def boom(_command: str) -> None:
        raise RuntimeError("gcode")

    manager = StepperCurrentManager(emit=boom)
    manager.register_motor("gate", stepper="stepper_mmu_0", run_current=0.5)
    assert manager.set_current("gate", run_current=0.7) is True


def test_load_from_config():
    config = {
        "stepper_currents": {
            "gate_0": {"stepper": "stepper_mmu_0", "run_current": 0.6, "hold_current": 0.3},
            "gate_1": 0.4,
        },
        "gear_stepper": "stepper_mmu_gear",
    }
    manager = StepperCurrentManager(config=config)
    assert manager.get_current("gate_0") == {"run_current": 0.6, "hold_current": 0.3}
    assert manager.get_current("gate_1") == {"run_current": 0.4, "hold_current": 0.0}
    status = manager.get_status()
    assert status["motors"]["gear"]["stepper"] == "stepper_mmu_gear"
    assert status["count"] == 3


def test_get_status_reports_last_command():
    manager = StepperCurrentManager(emit=lambda _c: None)
    manager.register_motor("gate", stepper="stepper_mmu_0", run_current=0.5)
    manager.set_current("gate", run_current=0.8)
    assert manager.get_status()["last_command"] == (
        "SET_TMC_CURRENT STEPPER=stepper_mmu_0 CURRENT=0.8 HOLDCURRENT=0.0"
    )


def test_build_factory_returns_instance():
    manager = build_stepper_current(config={})
    assert isinstance(manager, StepperCurrentManager)
