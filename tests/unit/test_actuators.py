"""Pruebas de actuadores: eSpooler PWM, environment/heater y LEDs."""

from __future__ import annotations

from dog_matrix.environment import EnvironmentManager
from dog_matrix.espooler import ESpooler
from dog_matrix.led_system import (
    EFFECT_BLINK,
    EFFECT_BREATHING,
    EFFECT_OFF,
    EFFECT_RAINBOW,
    LEDSystem,
)


# --- eSpooler --------------------------------------------------------------
def test_espooler_forward_curve_and_stop():
    power = []
    espooler = ESpooler(
        config={"enable_espooler": True, "espooler_scale": 2.0, "espooler_exponent": 2.0},
        set_pwm=power.append,
    )
    espooler.forward(0.5)
    assert abs(espooler.status.speed - (0.5 ** 2) * 2.0) < 1e-6
    assert power[-1] == espooler.status.speed
    espooler.stop()
    assert espooler.status.direction == "stop"
    assert power[-1] == 0.0


def test_espooler_burst_is_stopped_by_tick():
    espooler = ESpooler(config={"enable_espooler": True}, set_pwm=lambda _p: None)
    end = espooler.burst(2.0, 0.5)
    assert espooler.is_enabled()
    espooler.tick(end)
    assert espooler.status.direction == "stop"


def test_espooler_disabled_ignores_commands():
    espooler = ESpooler(config={"enable_espooler": False})
    espooler.forward(0.5)
    assert espooler.status.direction == "stop"


# --- Environment -----------------------------------------------------------
def test_environment_drying_control():
    heater = []
    env = EnvironmentManager(
        config={"enable_environment": True, "dryer_target_temp": 50, "dryer_hysteresis": 2},
        set_heater=heater.append,
    )
    env.update_readings(20.0, 50.0)
    env.start_drying(target_temp=50, duration_s=10.0)
    assert env.status.drying is True
    assert heater[-1] == 1.0  # por debajo del objetivo -> 100 %
    env.update_readings(50.0, 50.0)
    env.tick(env._now())
    assert env.status.dryer_power == 0.0  # objetivo alcanzado
    env.tick(env._dry_deadline)  # vence el ciclo
    assert env.status.drying is False


def test_environment_disabled_no_op():
    env = EnvironmentManager(config={"enable_environment": False})
    assert env.start_drying() is False
    assert env.status.drying is False


# --- LEDs ------------------------------------------------------------------
def test_led_blink_and_off():
    led = LEDSystem(None, {"led_count": 4})
    led.set_effect(EFFECT_BLINK, (10, 0, 0))
    assert led.frame_color(0, 0) == (10, 0, 0)
    assert led.frame_color(0, 5) == (0, 0, 0)
    led.set_effect(EFFECT_OFF)
    assert led.frame_color(0, 0) == (0, 0, 0)


def test_led_breathing_and_rainbow():
    led = LEDSystem(None, {"led_count": 4})
    led.set_effect(EFFECT_BREATHING, (100, 100, 100))
    assert led.frame_color(0, 0)[0] == 100  # pico de respiracion (tick 0)
    led.set_effect(EFFECT_RAINBOW)
    color = led.frame_color(0, 3)
    assert len(color) == 3


def test_led_update_and_segment():
    led = LEDSystem(None, {"led_count": 4})
    led.set_segment(1, 2, (5, 5, 5))
    assert led._base_colors[2] == (5, 5, 5)
    next_time = led.update(1.0)
    assert next_time > 1.0
    assert len(led.colors) == 4
