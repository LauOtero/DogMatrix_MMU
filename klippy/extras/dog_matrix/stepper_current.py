"""Gestion de corriente de los motores de la MMU (paridad Happy Hare).

Centraliza la corriente de *run* y *hold* de cada motor y la aplica enviando el
G-code ``SET_TMC_CURRENT`` (via un ``emit`` inyectable). Los motores iniciales
se pueden declarar en config mediante ``stepper_currents`` (dict) y, de forma
opcional, ``gear_stepper`` (str) para el motor de engranaje.

Sin dependencias de Klipper: el emisor de G-code se inyecta, de modo que el
modulo es determinista y testeable sin hardware.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

__all__ = ["StepperCurrentManager", "build_stepper_current"]

GEAR_MOTOR = "gear"


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    """Lee un valor de un ConfigWrapper o de un dict de forma segura."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            return default
    return default


class StepperCurrentManager:
    """Registra motores y aplica su corriente via ``SET_TMC_CURRENT``."""

    def __init__(self, config: Any = None, emit: Optional[Callable[[str], None]] = None) -> None:
        self.config = config
        self.emit = emit
        self.motors: Dict[str, Dict[str, Any]] = {}
        self.last_command: Optional[str] = None
        self._load_config(config)

    # -- Configuracion ------------------------------------------------------
    def _load_config(self, config: Any) -> None:
        raw = _cfg_get(config, "stepper_currents", None)
        if isinstance(raw, dict):
            for name, value in raw.items():
                self._register_from_value(str(name), value)
        gear = _cfg_get(config, "gear_stepper", None)
        if gear:
            existing = self.motors.get(GEAR_MOTOR)
            if existing is None:
                self.register_motor(GEAR_MOTOR, stepper=str(gear))
            else:
                existing["stepper"] = str(gear)

    def _register_from_value(self, name: str, value: Any) -> None:
        if isinstance(value, dict):
            self.register_motor(
                name,
                stepper=str(value.get("stepper", name)),
                run_current=float(value.get("run_current", 0.0) or 0.0),
                hold_current=float(value.get("hold_current", 0.0) or 0.0),
            )
        else:
            self.register_motor(name, stepper=name, run_current=float(value or 0.0))

    # -- API publica --------------------------------------------------------
    def register_motor(
        self,
        name: str,
        stepper: str = "",
        run_current: float = 0.0,
        hold_current: float = 0.0,
    ) -> None:
        """Registra (o reemplaza) un motor con su nombre de stepper y corrientes."""
        self.motors[name] = {
            "stepper": str(stepper or ""),
            "run_current": float(run_current or 0.0),
            "hold_current": float(hold_current or 0.0),
        }

    def set_current(
        self,
        name: str,
        run_current: Optional[float] = None,
        hold_current: Optional[float] = None,
    ) -> bool:
        """Actualiza la corriente del motor y emite el G-code si procede.

        Devuelve ``False`` si el motor no esta registrado.
        """
        motor = self.motors.get(name)
        if motor is None:
            return False
        if run_current is not None:
            motor["run_current"] = float(run_current)
        if hold_current is not None:
            motor["hold_current"] = float(hold_current)
        stepper = motor.get("stepper", "")
        if self.emit is not None and stepper:
            command = (
                f"SET_TMC_CURRENT STEPPER={stepper} "
                f"CURRENT={motor['run_current']} HOLDCURRENT={motor['hold_current']}"
            )
            self.last_command = command
            try:
                self.emit(command)
            except Exception:  # noqa: BLE001 - el emisor nunca debe romper el flujo
                pass
        return True

    def get_current(self, name: str) -> Dict[str, float]:
        """Devuelve las corrientes de *run*/*hold* del motor (0.0 si no existe)."""
        motor = self.motors.get(name)
        if motor is None:
            return {"run_current": 0.0, "hold_current": 0.0}
        return {
            "run_current": float(motor["run_current"]),
            "hold_current": float(motor["hold_current"]),
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "motors": {name: dict(data) for name, data in self.motors.items()},
            "count": len(self.motors),
            "last_command": self.last_command,
        }


def build_stepper_current(
    config: Any = None,
    emit: Optional[Callable[[str], None]] = None,
) -> StepperCurrentManager:
    """Construye un ``StepperCurrentManager`` a partir de config y emisor."""
    return StepperCurrentManager(config=config, emit=emit)
