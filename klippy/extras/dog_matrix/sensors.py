"""Lectura y normalizacion de sensores de filamento (patron Observer).

El acceso al GPIO se delega a la capa nativa (CFFI/chelper) para debounce y
deteccion de flancos con latencia minima; la interpretacion de eventos y los
callbacks se gestionan en Python (informe 6.6).

Requisitos: latencia de deteccion < 5 us (con interrupciones hardware),
debounce configurable 0.1-50.0 ms, consumo CPU < 0.1 %.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import _native

DEFAULT_DEBOUNCE_MS = 5.0
MIN_DEBOUNCE_MS = 0.1
MAX_DEBOUNCE_MS = 50.0


@dataclass
class SensorReading:
    """Lectura normalizada de un sensor."""

    name: str
    present: bool
    raw_state: bool
    timestamp: float
    debounce_ms: float = DEFAULT_DEBOUNCE_MS

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "present": self.present,
            "raw_state": self.raw_state,
            "timestamp": self.timestamp,
            "debounce_ms": self.debounce_ms,
        }


class _SensorChannel:
    """Estado interno por sensor (debounce + callbacks)."""

    def __init__(self, name: str, debounce_ms: float, active_low: bool = False) -> None:
        self.name = name
        self.debounce_ms = debounce_ms
        self.active_low = active_low
        self.stable = False
        self.candidate = False
        self.candidate_since = time.monotonic()
        self.initialized = False
        self.simulated: Optional[bool] = None
        self.interrupt_mode = False
        self.callbacks: List[Callable[[SensorReading], None]] = []


class SensorManager:
    """Gestiona el ciclo de lectura de sensores y notifica cambios."""

    def __init__(self, printer: Any, config: Any, profile: Any = None) -> None:
        self.printer = printer
        self.config = config
        self.channels: Dict[str, _SensorChannel] = {}
        self.event_count = 0
        self._build_channels(config, profile)
        # Inicializar buffer de sync-feedback si está configurado
        self.sync_feedback = self._init_sync_feedback(config, profile)

    def _init_sync_feedback(self, config: Any, profile: Any) -> Optional[Dict[str, Any]]:
        """Inicializa el buffer de sync-feedback para detección de enredos."""
        

    def _build_channels(self, config: Any, profile: Any) -> None:
        self.channels["toolhead"] = _SensorChannel("toolhead", DEFAULT_DEBOUNCE_MS)
        gates = profile.gates if profile is not None else 1
        gate_sensors = bool(getattr(profile, "capabilities", {}).get("gate_sensors", False)) if profile else False
        if gate_sensors or profile is None:
            for index in range(gates):
                name = f"gate_{index}"
                self.channels[name] = _SensorChannel(name, DEFAULT_DEBOUNCE_MS)

    # -- Simulacion (tests / banco) ----------------------------------------
    def set_simulated_state(self, name: str, present: bool) -> None:
        """Fija el estado de un sensor (modo simulacion / test)."""
        channel = self._channel(name)
        channel.simulated = bool(present)

    # -- API publica --------------------------------------------------------
    def _channel(self, name: str) -> _SensorChannel:
        if name not in self.channels:
            self.channels[name] = _SensorChannel(name, DEFAULT_DEBOUNCE_MS)
        return self.channels[name]

    def read(self, name: str) -> SensorReading:
        channel = self._channel(name)
        raw = self._raw_state(channel)
        now = time.monotonic()
        if not channel.initialized:
            # Primera lectura: inicializa el estado estable sin debounce.
            channel.initialized = True
            channel.stable = raw
            channel.candidate = raw
            channel.candidate_since = now
            return SensorReading(
                name=name, present=raw, raw_state=raw, timestamp=now, debounce_ms=channel.debounce_ms
            )
        stable, candidate, since = _native.debounce(
            channel.stable,
            channel.candidate,
            channel.candidate_since,
            now,
            raw,
            channel.debounce_ms / 1000.0,
        )
        channel.stable = stable
        channel.candidate = candidate
        channel.candidate_since = since
        return SensorReading(
            name=name,
            present=stable,
            raw_state=raw,
            timestamp=now,
            debounce_ms=channel.debounce_ms,
        )

    def _raw_state(self, channel: _SensorChannel) -> bool:
        if channel.simulated is not None:
            return channel.simulated
        # Sin hardware accesible desde este contexto: se mantiene el ultimo estado.
        return channel.stable

    def is_present(self, name: str) -> bool:
        return self.read(name).present

    def register_callback(self, name: str, callback: Callable[[SensorReading], None]) -> None:
        self._channel(name).callbacks.append(callback)

    def poll(self) -> List[SensorReading]:
        """Lee todos los sensores y dispara callbacks ante cambios de estado."""
        readings: List[SensorReading] = []
        for name, channel in self.channels.items():
            previous = channel.stable
            reading = self.read(name)
            readings.append(reading)
            if reading.present != previous:
                self.event_count += 1
                for callback in list(channel.callbacks):
                    try:
                        callback(reading)
                    except Exception:  # noqa: BLE001 - aislar fallos de callback
                        continue
        # Poll sync-feedback buffer si está configurado
        if self.sync_feedback is not None:
            self._poll_sync_feedback()
        return readings

    def _poll_sync_feedback(self) -> None:
        """Actualiza el buffer de sync-feedback y detecta tangles/clogs."""
        feedback = self.sync_feedback
        feedback["consecutive_violations"] = 0  # Reset cada poll, se incrementa en evaluate
        # TODO: En implementación real, aquí se leerían sensores de compresión/tensión
        # y se compararía contra los thresholds configurados
        # Por ahora, el módulo FlowGuard evaluará la divergencia y actualizará este buffer

    def get_sync_feedback_status(self) -> Dict[str, Any]:
        """Devuelve el estado actual del buffer de sync-feedback."""
        if self.sync_feedback is None:
            return {"enabled": False}
        feedback = dict(self.sync_feedback)
        feedback["enabled"] = True
        return feedback


    def start_polling(self) -> None:
        """Habilitado el modo de lectura (llamado en klippy:ready)."""
        for channel in self.channels.values():
            channel.candidate_since = time.monotonic()

    def get_raw_gpio_state(self) -> int:
        """Bitmask del estado crudo de todos los sensores."""
        mask = 0
        for index, (name, channel) in enumerate(sorted(self.channels.items())):
            if self._raw_state(channel):
                mask |= 1 << index
        return mask

    def set_debounce_time(self, name: str, time_ms: float) -> None:
        clamped = min(MAX_DEBOUNCE_MS, max(MIN_DEBOUNCE_MS, float(time_ms)))
        self._channel(name).debounce_ms = clamped

    def enable_interrupt_mode(self, name: str, enabled: bool) -> None:
        self._channel(name).interrupt_mode = bool(enabled)

    def get_status(self) -> Dict[str, Any]:
        return {
            name: channel.stable for name, channel in self.channels.items()
        }


__all__ = ["SensorManager", "SensorReading", "DEFAULT_DEBOUNCE_MS", "MIN_DEBOUNCE_MS", "MAX_DEBOUNCE_MS"]
