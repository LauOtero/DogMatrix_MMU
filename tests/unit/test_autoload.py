"""Pruebas del flujo de carga/gestion de filamento (auto-load, hub y buffer)."""

from __future__ import annotations

from dog_matrix import autoload as al


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _setup(**config_kwargs):
    clock = _Clock()
    sensors = al.SensorBank()
    motors = {"unit0": al.NullMotor(), "unit1": al.NullMotor()}
    hub = al.HubDevice(
        al.HubConfig(has_encoder=True, has_diameter_sensor=True, units=2),
        sensors=sensors,
    )
    buffer = al.BufferDevice(al.BufferConfig(tension_sensor=True, compression_sensor=True), sensors=sensors)
    router = al.MultiUnitRouter(buffer)
    controller = al.AutoLoadController(
        motor_for=lambda unit: motors.get(unit, al.NullMotor()),
        sensors=sensors,
        hub=hub,
        buffer=buffer,
        router=router,
        config=al.AutoLoadConfig(**config_kwargs),
        clock=clock,
    )
    return clock, sensors, motors, hub, buffer, controller


# --- Puntos 1 y 2: pre_gate arranca el motor; post_gate lo detiene ---------
def test_pre_gate_starts_motor():
    _, _, motors, _, _, controller = _setup()
    assert controller.on_pre_gate(2, unit="unit0") is True
    assert controller.state == al.STATE_FEEDING
    assert motors["unit0"].running is True
    assert motors["unit0"].last_speed == controller.config.feed_speed_mm_s


def test_post_gate_stops_motor_and_waits():
    _, sensors, motors, _, _, controller = _setup()
    controller.on_pre_gate(3, unit="unit0")
    sensors.set(al.POST_GATE_HUB, True)
    state = controller.poll()
    assert state == al.STATE_WAITING_SELECTION
    assert motors["unit0"].running is False


def test_pre_gate_timeout_is_resilient():
    clock, _, motors, _, _, controller = _setup(pre_gate_timeout_s=10.0)
    controller.on_pre_gate(1, unit="unit0")
    clock.advance(11.0)
    controller.poll()
    assert controller.state == al.STATE_FAULT
    assert controller.fault_reason == al.FAULT_PRE_GATE_TIMEOUT
    assert motors["unit0"].running is False


# --- Punto 3: encoder y diametro en tiempo real ---------------------------
def test_hub_encoder_and_diameter_readers():
    sensors = al.SensorBank()
    hub = al.HubDevice(
        al.HubConfig(has_encoder=True, has_diameter_sensor=True),
        sensors=sensors,
        encoder_reader=lambda: 12.5,
        diameter_reader=lambda: 1.75,
    )
    assert hub.read_encoder_mm() == 12.5
    assert hub.read_diameter_mm() == 1.75


def test_monitor_print_detects_diameter_and_no_movement():
    _, _, _, _, _, controller = _setup(diameter_min_mm=1.5, diameter_max_mm=2.0, min_encoder_mm=0.5)
    assert al.FAULT_DIAMETER_OUT_OF_RANGE in controller.monitor_print(moved_mm=10.0, diameter_mm=2.5)
    assert al.FAULT_NO_MOVEMENT in controller.monitor_print(moved_mm=0.0, diameter_mm=1.75)
    assert controller.monitor_print(moved_mm=10.0, diameter_mm=1.75) == []
    assert controller.counters["faults"] >= 2


# --- Puntos 4 y 5: seleccion y avance hasta el toolhead -------------------
def test_selection_moves_filament_to_toolhead():
    _, sensors, motors, _, _, controller = _setup()
    controller.on_pre_gate(0, unit="unit0")
    sensors.set(al.POST_GATE_HUB, True)
    controller.poll()
    assert controller.select_filament() is True
    assert controller.state == al.STATE_BOWDEN_FEED
    assert motors["unit0"].running is True
    sensors.set(al.TOOLHEAD_SENSOR, True)
    controller.poll()
    assert controller.state == al.STATE_READY
    assert motors["unit0"].running is False
    assert controller.counters["loads_completed"] == 1


def test_selection_requires_loaded_gate():
    _, _, _, _, _, controller = _setup()
    controller.on_pre_gate(4, unit="unit0")
    assert controller.select_filament(gate=5) is False  # aun no hay post_gate
    controller.sensors.set(al.POST_GATE_HUB, True)
    controller.poll()
    assert controller.select_filament(gate=5) is False  # gate distinto
    assert controller.select_filament(gate=4) is True


def test_bowden_timeout_is_resilient():
    clock, sensors, motors, _, _, controller = _setup(bowden_timeout_s=5.0)
    controller.on_pre_gate(0, unit="unit0")
    sensors.set(al.POST_GATE_HUB, True)
    controller.poll()
    controller.select_filament()
    clock.advance(6.0)
    controller.poll()
    assert controller.state == al.STATE_FAULT
    assert controller.fault_reason == al.FAULT_BOWDEN_TIMEOUT
    assert motors["unit0"].running is False


# --- Punto 6: buffer como splitter secundario (multi-unidad) --------------
def test_buffer_state_and_speed_factor():
    _, sensors, _, _, buffer, _ = _setup()
    assert buffer.state() == al.BUFFER_NEUTRAL
    assert buffer.speed_factor() == 1.0
    sensors.set(al.BUFFER_COMPRESSION, True)
    assert buffer.state() == al.BUFFER_COMPRESSED
    assert buffer.speed_factor() < 1.0
    sensors.set(al.BUFFER_COMPRESSION, False)
    sensors.set(al.BUFFER_TENSION, True)
    assert buffer.state() == al.BUFFER_EXPANDED
    assert buffer.speed_factor() > 1.0


def test_buffer_disabled_without_sensors():
    buffer = al.BufferDevice(al.BufferConfig(), al.SensorBank())
    assert buffer.state() == al.BUFFER_DISABLED
    assert buffer.speed_factor() == 1.0


def test_multiunit_router_is_exclusive():
    router = al.MultiUnitRouter()
    assert router.request_feed("unit0") is True
    assert router.request_feed("unit1") is False
    assert router.as_dict()["pending"] == ["unit1"]
    router.release("unit0")
    assert router.active_unit == "unit1"


def test_conflicting_unit_reports_buffer_conflict():
    clock, sensors, motors, hub, buffer, _ = _setup()
    router = al.MultiUnitRouter(buffer)
    router.request_feed("unit0")  # otra unidad ocupa el splitter
    controller = al.AutoLoadController(
        motor_for=lambda unit: motors.get(unit, al.NullMotor()),
        sensors=sensors,
        hub=hub,
        buffer=buffer,
        router=router,
        clock=clock,
    )
    assert controller.on_pre_gate(1, unit="unit1") is False
    assert controller.state == al.STATE_FAULT
    assert controller.fault_reason == al.FAULT_BUFFER_CONFLICT


def test_buffer_change_adjusts_motor_speed():
    _, sensors, motors, _, _, controller = _setup()
    controller.on_pre_gate(0, unit="unit0")
    sensors.set(al.BUFFER_COMPRESSION, True)
    controller.on_buffer_change()
    assert motors["unit0"].last_speed < controller.config.feed_speed_mm_s


# --- Punto 7: estado y ciclo de vida --------------------------------------
def test_reset_clears_state_and_releases_router():
    _, sensors, motors, _, _, controller = _setup()
    controller.on_pre_gate(0, unit="unit0")
    controller.reset()
    assert controller.state == al.STATE_IDLE
    assert controller.active_gate is None
    assert controller.router.active_unit is None
    assert motors["unit0"].running is False


def test_status_is_observable():
    _, sensors, _, _, _, controller = _setup()
    controller.on_pre_gate(1, unit="unit0")
    sensors.set(al.POST_GATE_HUB, True)
    controller.poll()
    status = controller.get_status()
    assert status["state"] == al.STATE_WAITING_SELECTION
    assert status["gate"] == 1
    assert status["hub"]["post_gate_active"] is True
    assert "counters" in status


# --- Reactor: servicio no bloqueante y dirigido por deadlines --------------
class _FakeReactor:
    """Reactor minimo compatible con la API de Klipper usada por el servicio."""

    NEVER = 1.0e18

    def __init__(self) -> None:
        self.now = 0.0
        self.timers: dict = {}
        self._next_id = 0
        self.callbacks: list = []

    def monotonic(self) -> float:
        return self.now

    def register_callback(self, callback):
        self.callbacks.append(callback)

    def run_callbacks(self) -> None:
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback(self.now)

    def register_timer(self, callback, when):
        self._next_id += 1
        self.timers[self._next_id] = (callback, when)
        return self._next_id

    def update_timer(self, handle, when):
        callback, _ = self.timers[handle]
        self.timers[handle] = (callback, when)

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _service_setup(**config_kwargs):
    clock, sensors, motors, hub, buffer, controller = _setup(**config_kwargs)
    reactor = _FakeReactor()
    service = al.AutoLoadService(controller, reactor)
    return reactor, sensors, motors, controller, service


def test_service_registers_single_timer_and_is_never_when_idle():
    reactor, _, _, controller, service = _service_setup()
    service.start()
    service.start()  # idempotente
    assert len(reactor.timers) == 1
    assert service.active is True
    assert service._timer(reactor.monotonic()) == reactor.NEVER
    assert controller.next_deadline() is None


def test_service_faults_at_deadline_without_blocking():
    reactor, _, motors, controller, service = _service_setup(pre_gate_timeout_s=10.0)
    service.start()
    controller.on_pre_gate(1, unit="unit0", eventtime=reactor.monotonic())
    assert controller.next_deadline() == 10.0
    reactor.advance(11.0)
    assert service._timer(reactor.monotonic()) == reactor.NEVER
    assert controller.state == al.STATE_FAULT
    assert controller.fault_reason == al.FAULT_PRE_GATE_TIMEOUT
    assert motors["unit0"].running is False


def test_service_returns_deadline_while_active():
    reactor, _, _, controller, service = _service_setup(pre_gate_timeout_s=10.0)
    service.start()
    controller.on_pre_gate(0, unit="unit0", eventtime=reactor.monotonic())
    assert service._timer(reactor.monotonic()) == 10.0


def test_kick_rearms_timer():
    reactor, _, _, controller, service = _service_setup()
    service.start()
    controller.on_pre_gate(0, unit="unit0", eventtime=reactor.monotonic())
    service.kick(5.0)
    assert reactor.timers[service._handle][1] == 5.0


def test_service_exception_triggers_emergency_stop():
    reactor, _, motors, controller, service = _service_setup()
    controller.on_pre_gate(0, unit="unit0", eventtime=0.0)
    service.start()

    def boom(_eventtime):
        raise RuntimeError("boom")

    controller.service = boom  # type: ignore[assignment]
    assert service._timer(reactor.monotonic()) == reactor.NEVER
    assert controller.state == al.STATE_FAULT
    assert controller.fault_reason == "service_exception"
    assert motors["unit0"].running is False


def test_stop_disarms_and_stops_motor():
    reactor, _, motors, controller, service = _service_setup()
    service.start()
    controller.on_pre_gate(0, unit="unit0", eventtime=0.0)
    service.stop()
    assert service.active is False
    assert reactor.timers[service._handle][1] == reactor.NEVER
    assert motors["unit0"].running is False
    assert controller.state == al.STATE_FAULT


def test_edge_handlers_full_flow():
    _, _, _, controller, _ = _service_setup()
    assert controller.on_pre_gate(2, unit="unit0", eventtime=0.0) is True
    assert controller.on_post_gate(0.1) is True
    assert controller.state == al.STATE_WAITING_SELECTION
    assert controller.on_post_gate(0.2) is False  # ya no esta en FEEDING
    assert controller.select_filament(eventtime=0.2) is True
    assert controller.on_toolhead(0.3) is True
    assert controller.state == al.STATE_READY


def test_emergency_stop_is_idempotent_and_releases_router():
    _, _, motors, controller, _ = _service_setup()
    controller.on_pre_gate(0, unit="unit0", eventtime=0.0)
    controller.emergency_stop("x")
    assert controller.state == al.STATE_FAULT
    assert controller.router.active_unit is None
    assert motors["unit0"].running is False
    controller.emergency_stop("y")  # no debe lanzar
    assert controller.state == al.STATE_FAULT


# --- Puente nativo con Klipper (buttons + reactor) ------------------------
class _FakeButtons:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def register_debounce_button(self, pin, handler, config=None):
        self.handlers[pin] = handler
        return f"handle:{pin}"


class _FakePrinter:
    def __init__(self) -> None:
        self.buttons = _FakeButtons()
        self.reactor = _FakeReactor()

    def load_object(self, config, name):
        return self.buttons

    def get_reactor(self):
        return self.reactor


def test_wire_pre_gate_buttons_uses_klipper_api():
    clock, sensors, motors, hub, buffer, controller = _setup()
    printer = _FakePrinter()
    handles = al.wire_pre_gate_buttons(
        printer, None, controller, {0: "^PA0", 1: "^PA1"}, service=None
    )
    assert len(handles) == 2
    # El handler del boton NO ejecuta trabajo: solo encola en el reactor.
    printer.buttons.handlers["^PA1"](1.0, True)
    assert controller.state == al.STATE_IDLE
    printer.reactor.run_callbacks()
    assert controller.state == al.STATE_FEEDING
    assert controller.active_gate == 1
    assert motors["unit0"].running is True
    # Un borde de liberacion no arranca nada.
    printer.buttons.handlers["^PA0"](2.0, False)
    printer.reactor.run_callbacks()
    assert controller.state == al.STATE_FEEDING  # sigue con gate 1
