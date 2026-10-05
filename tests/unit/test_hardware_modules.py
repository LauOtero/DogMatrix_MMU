"""Pruebas de encoder, sensores y selector."""

from __future__ import annotations

from types import SimpleNamespace

from dog_matrix.encoder import Encoder
from dog_matrix.sensors import MAX_DEBOUNCE_MS, SensorManager
from dog_matrix.selector import LinearSelector, RotarySelector, Selector, VirtualSelector


# --- Encoder ----------------------------------------------------------------
def test_encoder_position_and_error(tmp_path):
    encoder = Encoder(None, {"encoder_resolution": 0.5})
    encoder.set_simulated_position(100.0)
    assert abs(encoder.read_position() - 100.0) < 1e-6
    assert abs(encoder.get_error_mm(102.0) - 2.0) < 1e-3


def test_encoder_reset():
    encoder = Encoder(None, {"encoder_resolution": 0.5})
    encoder.set_simulated_position(50.0)
    encoder.read_position()
    encoder.reset()
    assert encoder.read_position() == 0.0
    assert encoder.get_raw_counts() == 0


def test_encoder_filter_alpha_bounds():
    encoder = Encoder(None, {})
    encoder.set_filter_alpha(5.0)
    assert encoder.filter_alpha == 1.0
    encoder.set_filter_alpha(-1.0)
    assert encoder.filter_alpha == 0.0


# --- Sensores ---------------------------------------------------------------
def test_sensor_debounce_requires_time():
    manager = SensorManager(None, {}, profile=None)
    manager.set_debounce_time("toolhead", 50.0)
    manager.read("toolhead")  # inicializa estado estable
    manager.set_simulated_state("toolhead", True)
    assert manager.read("toolhead").present is False  # aun no ha pasado el debounce


def test_sensor_debounce_confirms_after_time():
    import time

    manager = SensorManager(None, {}, profile=None)
    manager.set_debounce_time("toolhead", 0.5)
    manager.read("toolhead")
    manager.set_simulated_state("toolhead", True)
    manager.read("toolhead")  # marca candidato
    time.sleep(0.003)
    assert manager.is_present("toolhead") is True


def test_sensor_debounce_clamped():
    manager = SensorManager(None, {}, profile=None)
    manager.set_debounce_time("toolhead", 1000.0)
    assert manager.channels["toolhead"].debounce_ms == MAX_DEBOUNCE_MS


def test_sensor_callback_on_change():
    import time

    manager = SensorManager(None, {}, profile=None)
    manager.set_debounce_time("toolhead", 0.5)
    manager.poll()  # inicializa
    events = []
    manager.register_callback("toolhead", lambda reading: events.append(reading.present))
    manager.set_simulated_state("toolhead", True)
    manager.poll()  # candidato
    time.sleep(0.003)
    manager.poll()  # confirma -> callback
    assert events == [True]


def test_sensor_raw_gpio_mask():
    manager = SensorManager(None, {}, profile=None)
    manager.set_simulated_state("toolhead", True)
    assert manager.get_raw_gpio_state() != 0


# --- Selector ---------------------------------------------------------------
def test_linear_selector_position():
    strategy = LinearSelector(None, {"gate_pitch_mm": 20.0})
    assert strategy.select_gate(3)
    assert strategy.get_position() == 60.0
    assert strategy.is_at_gate(3)


def test_rotary_selector_position():
    strategy = RotarySelector(None, {}, gates=8)
    assert strategy.select_gate(2)
    assert strategy.get_position() == 90.0


def test_virtual_selector():
    strategy = VirtualSelector(None, {})
    assert strategy.select_gate(5)
    assert strategy.is_at_gate(5)


def test_selector_picks_strategy_from_profile():
    profile = SimpleNamespace(
        topology_type="selector", selector_type="rotary", gates=8, topology={}
    )
    selector = Selector(None, {}, motion=None, profile=profile)
    assert isinstance(selector._strategy, RotarySelector)
    assert selector.select_gate(1)
