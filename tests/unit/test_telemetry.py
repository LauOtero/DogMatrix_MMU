"""Pruebas de la telemetria de series temporales (buffer circular)."""

from __future__ import annotations

import pytest

from dog_matrix.telemetry import RingChannel, Sample, TelemetryBuffer


def test_ring_overwrites_and_keeps_order():
    ring = RingChannel(capacity=3)
    for i in range(1, 6):
        ring.append(value=i, timestamp=float(i))
    samples = ring.samples()
    assert [s.value for s in samples] == [3.0, 4.0, 5.0]
    assert [s.timestamp for s in samples] == [3.0, 4.0, 5.0]
    assert isinstance(samples[0], Sample)


def test_stats_known_values():
    ring = RingChannel()
    for value in (2, 4, 4, 4, 5, 5, 7, 9):
        ring.append(value=value, timestamp=0.0)
    stats = ring.stats()
    assert stats["count"] == 8
    assert stats["min"] == 2
    assert stats["max"] == 9
    assert stats["mean"] == pytest.approx(5.0)
    assert stats["std"] == pytest.approx(2.0)  # desviacion poblacional
    assert stats["first_ts"] == 0.0 and stats["last_ts"] == 0.0


def test_stats_empty_is_zero():
    stats = RingChannel().stats()
    assert stats == {
        "count": 0,
        "min": 0,
        "max": 0,
        "mean": 0,
        "std": 0,
        "first_ts": 0,
        "last_ts": 0,
    }


def test_downsample_length_and_mean():
    ring = RingChannel()
    for value in (1, 2, 3, 4):
        ring.append(value=value, timestamp=0.0)
    result = ring.downsample(2)
    assert result == [1.5, 3.5]
    assert ring.downsample(0) == []
    assert RingChannel().downsample(4) == []


def test_to_csv_header_and_rows():
    ring = RingChannel()
    ring.append(value=1.5, timestamp=10.0)
    ring.append(value=2.5, timestamp=11.0)
    assert ring.to_csv() == "timestamp,value\n10.0,1.5\n11.0,2.5"


def test_record_creates_channel_on_demand():
    buffer = TelemetryBuffer(capacity=8)
    assert buffer.channels() == []
    buffer.record("sync", 1.0, timestamp=2.0)
    buffer.record("sync", 3.0)
    assert buffer.channels() == ["sync"]
    assert buffer.series("sync") == [(2.0, 1.0), (0.0, 3.0)]
    assert buffer.channel("sync").capacity == 8
    # canal desconocido se crea vacio al consultarlo.
    assert buffer.series("otro") == []


def test_clear_single_and_all():
    buffer = TelemetryBuffer()
    buffer.record("a", 1.0, timestamp=1.0)
    buffer.record("b", 2.0, timestamp=1.0)
    buffer.clear("a")
    assert buffer.series("a") == []
    assert buffer.series("b") == [(1.0, 2.0)]
    buffer.clear()
    assert buffer.channels() == []


def test_get_status_includes_stats_per_channel():
    buffer = TelemetryBuffer()
    buffer.record("x", 2.0, timestamp=1.0)
    buffer.record("x", 4.0, timestamp=2.0)
    status = buffer.get_status()
    assert set(status["channels"]) == {"x"}
    assert status["channels"]["x"]["count"] == 2
    assert status["channels"]["x"]["mean"] == pytest.approx(3.0)


def test_buffer_delegates_downsample_and_csv():
    buffer = TelemetryBuffer()
    for i in range(1, 5):
        buffer.record("c", float(i), timestamp=float(i))
    assert buffer.downsample("c", 2) == [1.5, 3.5]
    assert buffer.to_csv("c").splitlines()[0] == "timestamp,value"
