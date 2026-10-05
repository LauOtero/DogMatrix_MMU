"""Telemetria de series temporales por canal (plots y feedback de sync).

Equivalente en Python a ``make plot_sync`` y ``mmu:sync_feedback``: mantiene un
buffer circular de capacidad fija por canal, con estadisticas, downsample y
export CSV. Estado puro en memoria, determinista y sin dependencias de Klipper.

El registro es O(1) amortizado mediante ``collections.deque(maxlen=capacity)``
preasignado, que sobrescribe la muestra mas antigua al llenarse.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple


@dataclass
class Sample:
    timestamp: float
    value: float


class RingChannel:
    """Buffer circular de capacidad fija con estadisticas y export."""

    def __init__(self, capacity: int = 1024) -> None:
        self.capacity = max(1, int(capacity))
        self._buffer: Deque[Sample] = deque(maxlen=self.capacity)

    def append(self, value: float, timestamp: float) -> None:
        self._buffer.append(Sample(timestamp=float(timestamp), value=float(value)))

    def samples(self) -> List[Sample]:
        return list(self._buffer)

    def stats(self) -> Dict[str, Any]:
        count = len(self._buffer)
        if count == 0:
            return {
                "count": 0,
                "min": 0,
                "max": 0,
                "mean": 0,
                "std": 0,
                "first_ts": 0,
                "last_ts": 0,
            }
        values = [s.value for s in self._buffer]
        mean = sum(values) / count
        variance = sum((v - mean) ** 2 for v in values) / count
        return {
            "count": count,
            "min": min(values),
            "max": max(values),
            "mean": mean,
            "std": math.sqrt(variance),
            "first_ts": self._buffer[0].timestamp,
            "last_ts": self._buffer[-1].timestamp,
        }

    def downsample(self, buckets: int) -> List[float]:
        count = len(self._buffer)
        if buckets <= 0 or count == 0:
            return []
        chunks = min(int(buckets), count)
        values = [s.value for s in self._buffer]
        result: List[float] = []
        for i in range(chunks):
            start = i * count // chunks
            end = (i + 1) * count // chunks
            chunk = values[start:end]
            result.append(sum(chunk) / len(chunk))
        return result

    def to_csv(self) -> str:
        lines = ["timestamp,value"]
        lines.extend(f"{s.timestamp},{s.value}" for s in self._buffer)
        return "\n".join(lines)

    def clear(self) -> None:
        self._buffer.clear()


class TelemetryBuffer:
    """Conjunto de canales de telemetria creados on-demand."""

    def __init__(self, capacity: int = 1024) -> None:
        self.capacity = max(1, int(capacity))
        self._channels: Dict[str, RingChannel] = {}

    # -- Registro -----------------------------------------------------------
    def record(self, channel: str, value: float, timestamp: Optional[float] = None) -> None:
        self.channel(channel).append(value, 0.0 if timestamp is None else timestamp)

    def channel(self, name: str) -> RingChannel:
        ring = self._channels.get(name)
        if ring is None:
            ring = RingChannel(self.capacity)
            self._channels[name] = ring
        return ring

    # -- Consultas ----------------------------------------------------------
    def series(self, channel: str) -> List[Tuple[float, float]]:
        return [(s.timestamp, s.value) for s in self.channel(channel).samples()]

    def stats(self, channel: str) -> Dict[str, Any]:
        return self.channel(channel).stats()

    def to_csv(self, channel: str) -> str:
        return self.channel(channel).to_csv()

    def downsample(self, channel: str, buckets: int) -> List[float]:
        return self.channel(channel).downsample(buckets)

    def channels(self) -> List[str]:
        return sorted(self._channels)

    def clear(self, channel: Optional[str] = None) -> None:
        if channel is None:
            self._channels.clear()
        else:
            ring = self._channels.get(channel)
            if ring is not None:
                ring.clear()

    def get_status(self) -> Dict[str, Any]:
        return {
            "channels": {name: ring.stats() for name, ring in sorted(self._channels.items())}
        }


__all__ = ["Sample", "RingChannel", "TelemetryBuffer"]
