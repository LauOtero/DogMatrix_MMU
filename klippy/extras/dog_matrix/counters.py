"""Contadores de consumo/mantenimiento con limite, aviso y pausa.

Permite definir contadores de usuario (p. ej. vida de una cuchilla, ciclos de
servo) con un limite, un aviso y una accion de pausa. Estado puro en memoria,
serializable para persistencia (``as_dict``/``load``) sin dependencias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Counter:
    name: str
    value: int = 0
    limit: int = -1          # -1 = sin limite
    warning: str = ""
    pause: bool = False
    warned: bool = False

    def reached(self) -> bool:
        return self.limit >= 0 and self.value >= self.limit

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "limit": self.limit,
            "warning": self.warning,
            "pause": self.pause,
            "warned": self.warned,
        }


@dataclass
class CounterEvent:
    name: str
    value: int
    limit: int
    warning: str
    pause: bool
    triggered: bool


class CounterStore:
    """Almacen de contadores (determinista, sin E/S)."""

    def __init__(self, on_event: Optional[Callable[[CounterEvent], None]] = None) -> None:
        self._counters: Dict[str, Counter] = {}
        self._on_event = on_event

    # -- Definicion ---------------------------------------------------------
    def define(self, name: str, limit: int = -1, warning: str = "", pause: bool = False) -> Counter:
        counter = self._counters.get(name)
        if counter is None:
            counter = Counter(name=name)
            self._counters[name] = counter
        counter.limit = int(limit)
        counter.warning = str(warning)
        counter.pause = bool(pause)
        return counter

    def get(self, name: str) -> Optional[Counter]:
        return self._counters.get(name)

    def names(self) -> List[str]:
        return sorted(self._counters)

    # -- Operaciones --------------------------------------------------------
    def incr(self, name: str, amount: int = 1) -> CounterEvent:
        counter = self._counters.setdefault(name, Counter(name=name))
        counter.value += max(0, int(amount))
        triggered = False
        if counter.reached() and not counter.warned:
            counter.warned = True
            triggered = True
            if self._on_event is not None:
                self._on_event(
                    CounterEvent(counter.name, counter.value, counter.limit,
                                 counter.warning, counter.pause, triggered)
                )
        return CounterEvent(counter.name, counter.value, counter.limit, counter.warning, counter.pause, triggered)

    def reset(self, name: str) -> bool:
        counter = self._counters.get(name)
        if counter is None:
            return False
        counter.value = 0
        counter.warned = False
        return True

    def delete(self, name: str) -> bool:
        return self._counters.pop(name, None) is not None

    def reset_all(self) -> None:
        for counter in self._counters.values():
            counter.value = 0
            counter.warned = False

    # -- Serializacion ------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {name: counter.as_dict() for name, counter in sorted(self._counters.items())}

    def load(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        for name, payload in data.items():
            if not isinstance(payload, dict):
                continue
            counter = self.define(
                str(name),
                limit=int(payload.get("limit", -1)),
                warning=str(payload.get("warning", "")),
                pause=bool(payload.get("pause", False)),
            )
            counter.value = int(payload.get("value", 0))
            counter.warned = bool(payload.get("warned", False))


__all__ = ["Counter", "CounterEvent", "CounterStore"]
