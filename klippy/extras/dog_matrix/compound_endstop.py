"""Endstop compuesto de la MMU (paridad Happy Hare): gate + encoder.

Combina el sensor del *gate* (deteccion directa de filamento) con el encoder de
la MMU (deteccion indirecta por distancia medida). Se considera activado si el
sensor del gate esta presente **o** si el encoder ha recorrido al menos la
distancia esperada (``measured_mm >= expected_mm`` cuando ``expected_mm > 0``).

Determinismo estricto: todas las entradas (lectura del sensor y distancia
medida) se inyectan; no hay ``sleep`` ni acceso a hardware. Las excepciones de
callbacks opcionales se aislan para no propagarlas al flujo de movimiento.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

__all__ = ["CompoundEndstop", "build_compound_endstop"]


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


class CompoundEndstop:
    """Endstop virtual que agrega sensor de gate y encoder de la MMU."""

    def __init__(
        self,
        sensor_reader: Optional[Callable[[], bool]] = None,
        encoder: Any = None,
        config: Any = None,
    ) -> None:
        self.sensor_reader = sensor_reader
        self.encoder = encoder
        self.config = config
        self.triggers = 0
        self.history: List[Dict[str, float]] = []
        self._triggered = False
        self._last_sensor = False
        self._encoder_mm = 0.0

    # -- Lectura ------------------------------------------------------------
    def _read_sensor(self) -> bool:
        """Lee el sensor del gate aislando cualquier excepcion del callback."""
        if not callable(self.sensor_reader):
            return False
        try:
            return bool(self.sensor_reader())
        except Exception:  # noqa: BLE001 - el lector nunca debe romper el flujo
            return False

    def triggered(self, measured_mm: float = 0.0, expected_mm: float = 0.0) -> bool:
        """Evalua el endstop compuesto para la distancia medida.

        Devuelve ``True`` si el sensor del gate esta activo o si el encoder ha
        alcanzado la distancia esperada (solo cuando ``expected_mm > 0``).
        """
        sensor = self._read_sensor()
        encoder_hit = False
        if self.encoder is not None and expected_mm > 0.0:
            encoder_hit = float(measured_mm) >= float(expected_mm)
        result = bool(sensor or encoder_hit)
        self._last_sensor = sensor
        self._encoder_mm = float(measured_mm)
        self._triggered = result
        if result:
            self.triggers += 1
        return result

    # -- Sincronizacion -----------------------------------------------------
    def sync(self, measured_mm: float, expected_mm: float = 0.0) -> None:
        """Registra el estado actual en el historial y evalua el endstop."""
        result = self.triggered(measured_mm=measured_mm, expected_mm=expected_mm)
        self.history.append(
            {
                "measured_mm": float(measured_mm),
                "expected_mm": float(expected_mm),
                "triggered": bool(result),
            }
        )

    def reset(self) -> None:
        """Reinicia contadores, historial y estado interno."""
        self.triggers = 0
        self.history = []
        self._triggered = False
        self._last_sensor = False
        self._encoder_mm = 0.0

    # -- Estado -------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "triggered": self._triggered,
            "sensor": self._last_sensor,
            "encoder_mm": self._encoder_mm,
            "triggers": self.triggers,
        }


def build_compound_endstop(
    sensor_reader: Optional[Callable[[], bool]] = None,
    encoder: Any = None,
    config: Any = None,
) -> CompoundEndstop:
    """Construye un ``CompoundEndstop`` a partir de sus dependencias."""
    return CompoundEndstop(sensor_reader=sensor_reader, encoder=encoder, config=config)
