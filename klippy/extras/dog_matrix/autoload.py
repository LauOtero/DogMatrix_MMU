"""Flujo de carga y gestion de filamento (auto-load) para Dog Matrix MMU.

Implementa el ciclo completo de carga/gestion descrito en la especificacion:

1. **pre_gate**: se activa cuando el usuario introduce el filamento en la unidad;
   es la condicion inicial para arrancar la carga automatizada.
2. Al activarse el pre_gate se **arranca el motor de la unidad** y se mantiene en
   marcha **hasta que se activa el post_gate** (sensor en el hub/splitter). Con
   **resiliencia**: si no llega en ``pre_gate_timeout_s`` el motor se detiene y se
   entra en FAULT (nunca queda encendido indefinidamente).
3. El hub/splitter puede integrar **encoder** y **sensor de diametro** para
   controles en tiempo real durante la impresion.
4. Alcanzado el post_gate, el sistema queda en **espera de seleccion**.
5. Confirmada la seleccion, se activa el avance **hub -> toolhead**.
6. El **buffer intermedio de tension/compresion** actua como **splitter
   secundario** (multi-unidad): reparte los filamentos de cada unidad y online
   regula la velocidad del motor.
7. Toda la maquina de estados es observable y testeable.

Diseno alineado con las convenciones de un extra de Klipper (lecciones de Happy
Hare y de ``extras/`` de Klipper):

- **Dirigido por reactor y por eventos**: la deteccion usa *callbacks* de borde
  (``buttons``) y el servicio registra **un unico timer** que despierta solo en
  el *deadline* util (o ``reactor.NEVER`` en reposo). **Nunca se bloquea el hilo
  principal** ni se hace *busy-wait*.
- **Determinismo extremo**: todas las transiciones son funciones puras de
  ``(estado, entradas, eventtime)``; los plazos se derivan del reloj del reactor
  (sin ``sleep``, sin aleatoriedad, sin estado oculto).
- **Resiliencia absoluta**: watchdog de tiempo, ``emergency_stop`` idempotente,
  aislamiento de excepciones y parada garantizada del motor.
- **Autocontenido**: usa objetos inyectados (reactor, reloj, motor, sensores),
  de modo que las librerias de Klipper se usan en runtime sin acoplar el modulo
  (y sin importarlas en las pruebas unitarias).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# --- Estados del flujo ------------------------------------------------------
STATE_IDLE = "idle"
STATE_FEEDING = "feeding"                     # pre_gate activo, motor en marcha
STATE_WAITING_SELECTION = "waiting_selection"  # post_gate alcanzado
STATE_BOWDEN_FEED = "bowden_feed"             # seleccion confirmada -> toolhead
STATE_READY = "ready"                         # filamento en el toolhead
STATE_FAULT = "fault"

# --- Causas de fallo --------------------------------------------------------
FAULT_NONE = ""
FAULT_PRE_GATE_TIMEOUT = "pre_gate_timeout"
FAULT_BOWDEN_TIMEOUT = "bowden_timeout"
FAULT_DIAMETER_OUT_OF_RANGE = "diameter_out_of_range"
FAULT_NO_MOVEMENT = "no_movement"
FAULT_BUFFER_CONFLICT = "buffer_conflict"

# --- Estados del buffer de tension/compresion ------------------------------
BUFFER_NEUTRAL = "neutral"
BUFFER_COMPRESSED = "compressed"
BUFFER_EXPANDED = "expanded"
BUFFER_DISABLED = "disabled"


def pre_gate_sensor(gate: int) -> str:
    return f"pre_gate_{gate}"


def post_gate_sensor(gate: int) -> str:
    return f"post_gate_{gate}"


POST_GATE_HUB = "post_gate_hub"
TOOLHEAD_SENSOR = "toolhead"
EXTRUDER_ENTRY_SENSOR = "extruder_entry"
BUFFER_TENSION = "buffer_tension"
BUFFER_COMPRESSION = "buffer_compression"


# ---------------------------------------------------------------------------
# Motores
# ---------------------------------------------------------------------------
class MotorDriver:
    """Contrato minimo de un motor de carga/descarga."""

    def start(self, speed_mm_s: float) -> None:  # pragma: no cover - interfaz
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interfaz
        raise NotImplementedError

    @property
    def running(self) -> bool:  # pragma: no cover - interfaz
        raise NotImplementedError


class NullMotor(MotorDriver):
    """Motor no conectado (pruebas / hardware ausente)."""

    def __init__(self) -> None:
        self._running = False
        self.last_speed = 0.0
        self.start_calls = 0
        self.stop_calls = 0

    def start(self, speed_mm_s: float) -> None:
        self._running = True
        self.last_speed = float(speed_mm_s)
        self.start_calls += 1

    def stop(self) -> None:
        self._running = False
        self.last_speed = 0.0
        self.stop_calls += 1

    @property
    def running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# Sensores
# ---------------------------------------------------------------------------
class SensorBank:
    """Banco de sensores conmutables (para pruebas sin hardware)."""

    def __init__(self, values: Optional[Dict[str, bool]] = None) -> None:
        self.values: Dict[str, bool] = dict(values or {})

    def set(self, name: str, active: bool) -> None:
        self.values[name] = bool(active)

    def get(self, name: str) -> bool:
        return bool(self.values.get(name, False))


# ---------------------------------------------------------------------------
# Hub / splitter principal
# ---------------------------------------------------------------------------
@dataclass
class HubConfig:
    name: str = "hub"
    #: Sensor de salida compartido (post_gate fisico del hub/splitter).
    shared_exit_sensor: bool = True
    #: Encoder para validacion de movimiento en tiempo real.
    has_encoder: bool = False
    #: Sensor de diametro de filamento.
    has_diameter_sensor: bool = False
    #: Numero de unidades que convergen en el hub (multi-MMU).
    units: int = 1


class HubDevice:
    """Hub/splitter principal: punto donde llega el filamento de cada unidad."""

    def __init__(
        self,
        config: Optional[HubConfig] = None,
        sensors: Optional[SensorBank] = None,
        encoder_reader: Optional[Callable[[], float]] = None,
        diameter_reader: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config or HubConfig()
        self.sensors = sensors or SensorBank()
        self._encoder_reader = encoder_reader
        self._diameter_reader = diameter_reader

    # -- Post-gate ----------------------------------------------------------
    def post_gate_active(self) -> bool:
        """True cuando el filamento ha alcanzado el hub/splitter."""
        if not self.config.shared_exit_sensor:
            return False
        return self.sensors.get(POST_GATE_HUB)

    # -- Telemetria en tiempo real -----------------------------------------
    def read_encoder_mm(self) -> float:
        return float(self._encoder_reader()) if self._encoder_reader else 0.0

    def read_diameter_mm(self) -> float:
        return float(self._diameter_reader()) if self._diameter_reader else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.config.name,
            "post_gate_active": self.post_gate_active(),
            "has_encoder": self.config.has_encoder,
            "has_diameter_sensor": self.config.has_diameter_sensor,
            "units": self.config.units,
        }


# ---------------------------------------------------------------------------
# Buffer intermedio (splitter secundario de tension/compresion)
# ---------------------------------------------------------------------------
@dataclass
class BufferConfig:
    name: str = "buffer"
    tension_sensor: bool = False
    compression_sensor: bool = False
    analog: bool = False
    #: Actua como splitter secundario que fusiona la salida de todas las unidades.
    secondary_splitter: bool = True
    compression_speed_factor: float = 0.5
    expanded_speed_factor: float = 1.5


class BufferDevice:
    """Buffer de tension/compresion: splitter secundario entre hub y toolhead."""

    def __init__(self, config: Optional[BufferConfig] = None, sensors: Optional[SensorBank] = None) -> None:
        self.config = config or BufferConfig()
        self.sensors = sensors or SensorBank()

    def state(self) -> str:
        if not (self.config.tension_sensor or self.config.compression_sensor):
            return BUFFER_DISABLED
        if self.config.compression_sensor and self.sensors.get(BUFFER_COMPRESSION):
            return BUFFER_COMPRESSED
        if self.config.tension_sensor and self.sensors.get(BUFFER_TENSION):
            return BUFFER_EXPANDED
        return BUFFER_NEUTRAL

    def speed_factor(self) -> float:
        """Factor de velocidad del motor segun el estado del buffer."""
        state = self.state()
        if state == BUFFER_COMPRESSED:
            return self.config.compression_speed_factor
        if state == BUFFER_EXPANDED:
            return self.config.expanded_speed_factor
        return 1.0

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.config.name, "state": self.state(), "speed_factor": self.speed_factor()}


# ---------------------------------------------------------------------------
# Reparto multi-unidad (splitter secundario)
# ---------------------------------------------------------------------------
class MultiUnitRouter:
    """Garantiza que solo una unidad empuja filamento al splitter a la vez.

    El buffer/hub fusiona las salidas de todas las unidades hacia el toolhead,
    pero el reparto debe ser exclusivo para no bloquear el camino.
    """

    def __init__(self, buffer: Optional[BufferDevice] = None) -> None:
        self.buffer = buffer
        self.active_unit: Optional[str] = None
        self._pending: List[str] = []

    def request_feed(self, unit_id: str) -> bool:
        if self.active_unit is None or self.active_unit == unit_id:
            self.active_unit = unit_id
            return True
        if unit_id not in self._pending:
            self._pending.append(unit_id)
        return False

    def release(self, unit_id: str) -> None:
        if self.active_unit == unit_id:
            self.active_unit = self._pending.pop(0) if self._pending else None

    def as_dict(self) -> Dict[str, Any]:
        return {"active_unit": self.active_unit, "pending": list(self._pending)}


# ---------------------------------------------------------------------------
# Configuracion del auto-load
# ---------------------------------------------------------------------------
@dataclass
class AutoLoadConfig:
    feed_speed_mm_s: float = 70.0
    bowden_speed_mm_s: float = 80.0
    #: Resiliencia: el motor nunca queda encendido mas alla de este tiempo.
    pre_gate_timeout_s: float = 30.0
    bowden_timeout_s: float = 60.0
    diameter_min_mm: float = 1.5
    diameter_max_mm: float = 2.0
    #: Margen de movimiento minimo para considerar suministro valido (mm).
    min_encoder_mm: float = 0.5
    speed_factor_min: float = 0.4
    speed_factor_max: float = 1.6


# ---------------------------------------------------------------------------
# Controlador de auto-load
# ---------------------------------------------------------------------------
class AutoLoadController:
    """Orquesta carga (pre_gate -> post_gate), espera, seleccion y avance."""

    def __init__(
        self,
        motor_for: Optional[Callable[[str], MotorDriver]] = None,
        sensors: Optional[SensorBank] = None,
        hub: Optional[HubDevice] = None,
        buffer: Optional[BufferDevice] = None,
        router: Optional[MultiUnitRouter] = None,
        config: Optional[AutoLoadConfig] = None,
        clock: Callable[[], float] = time.monotonic,
        diagnostics: Any = None,
    ) -> None:
        self.config = config or AutoLoadConfig()
        self.sensors = sensors or SensorBank()
        self.hub = hub or HubDevice(sensors=self.sensors)
        self.buffer = buffer or BufferDevice(sensors=self.sensors)
        self.router = router or MultiUnitRouter(self.buffer)
        self._motor_for = motor_for or (lambda _unit: NullMotor())
        self._clock = clock
        self.diagnostics = diagnostics

        self.state = STATE_IDLE
        self.active_gate: Optional[int] = None
        self.active_unit: Optional[str] = None
        self.fault_reason = FAULT_NONE
        self._feed_started_at: float = 0.0
        self._bowden_started_at: float = 0.0
        # Deadlines absolutos (reloj del reactor) para despertar solo cuando toca.
        self._feed_deadline: float = 0.0
        self._bowden_deadline: float = 0.0
        self.counters: Dict[str, int] = {
            "loads_started": 0,
            "loads_completed": 0,
            "selections": 0,
            "faults": 0,
        }

    # -- Motores ------------------------------------------------------------
    def _motor(self) -> MotorDriver:
        return self._motor_for(self.active_unit or "unit0")

    def _start_motor(self, speed_mm_s: float) -> None:
        factor = self.buffer.speed_factor() if self.buffer else 1.0
        factor = min(max(factor, self.config.speed_factor_min), self.config.speed_factor_max)
        self._motor().start(speed_mm_s * factor)

    def _stop_motor(self) -> None:
        try:
            self._motor().stop()
        except Exception:  # noqa: BLE001 - el motor nunca debe quedar encendido
            pass

    # -- Paso 1/2: pre_gate arranca el motor --------------------------------
    def on_pre_gate(self, gate: int, unit: str = "unit0", eventtime: Optional[float] = None) -> bool:
        """Filamento insertado fisicamente: arranca la carga automatica.

        Pensado para llamarse desde un *callback* de borde del sensor (Klipper
        ``buttons``) o desde un comando. No bloquea: solo fija estado y deadline.
        """
        if self.state in (STATE_FEEDING, STATE_BOWDEN_FEED):
            return False
        if not self.router.request_feed(unit):
            self._set_fault(FAULT_BUFFER_CONFLICT, unit=unit, gate=gate)
            return False
        now = self._now(eventtime)
        self.active_gate = int(gate)
        self.active_unit = unit
        self.fault_reason = FAULT_NONE
        self.state = STATE_FEEDING
        self._feed_started_at = now
        self._feed_deadline = now + self.config.pre_gate_timeout_s
        self._start_motor(self.config.feed_speed_mm_s)
        self.counters["loads_started"] += 1
        self._log("info", "pre_gate_activated", gate=gate, unit=unit)
        return True

    # -- Paso 3/4: post_gate -> espera de seleccion -------------------------
    def on_post_gate(self, eventtime: Optional[float] = None) -> bool:
        """Borde del post_gate (hub/splitter): detiene el motor y espera seleccion."""
        if self.state != STATE_FEEDING:
            return False
        self._stop_motor()
        self.state = STATE_WAITING_SELECTION
        self._feed_deadline = 0.0
        self._log("info", "post_gate_reached", gate=self.active_gate, unit=self.active_unit)
        return True

    def on_toolhead(self, eventtime: Optional[float] = None) -> bool:
        """Borde del sensor de toolhead: filamento cargado hasta el cabezal."""
        if self.state != STATE_BOWDEN_FEED:
            return False
        self._stop_motor()
        self.state = STATE_READY
        self._bowden_deadline = 0.0
        self.counters["loads_completed"] += 1
        self._log("info", "toolhead_reached", gate=self.active_gate, unit=self.active_unit)
        return True

    def _evaluate(self, now: float) -> str:
        """Evaluacion determinista de la maquina de estados en ``now``."""
        if self.state == STATE_FEEDING:
            if self.hub.post_gate_active():
                self.on_post_gate(now)
            elif self._feed_deadline and now >= self._feed_deadline:
                self._set_fault(FAULT_PRE_GATE_TIMEOUT)
        elif self.state == STATE_BOWDEN_FEED:
            if self.sensors.get(TOOLHEAD_SENSOR):
                self.on_toolhead(now)
            elif self._bowden_deadline and now >= self._bowden_deadline:
                self._set_fault(FAULT_BOWDEN_TIMEOUT)
        return self.state

    def poll(self, now: Optional[float] = None) -> str:
        """Compatibilidad sincrona: evalua el estado leyendo sensores por nivel."""
        return self._evaluate(self._now(now))

    def next_deadline(self) -> Optional[float]:
        """Proximo instante en que el servicio debe despertar (o ``None``)."""
        if self.state == STATE_FEEDING and self._feed_deadline:
            return self._feed_deadline
        if self.state == STATE_BOWDEN_FEED and self._bowden_deadline:
            return self._bowden_deadline
        return None

    def service(self, eventtime: float) -> Optional[float]:
        """Tick del servicio (llamado por el timer del reactor).

        Devuelve el proximo deadline o ``None`` en reposo -> el servicio
        devuelve ``reactor.NEVER`` (cero despertares innecesarios).
        """
        self._evaluate(float(eventtime))
        return self.next_deadline()

    def _now(self, eventtime: Optional[float]) -> float:
        return float(eventtime) if eventtime is not None else self._clock()

    # -- Paso 5: seleccion -> avance hub -> toolhead ------------------------
    def select_filament(self, gate: Optional[int] = None, eventtime: Optional[float] = None) -> bool:
        if self.state != STATE_WAITING_SELECTION:
            return False
        if gate is not None and self.active_gate is not None and int(gate) != self.active_gate:
            # La seleccion debe corresponder al filamento ya cargado hasta el hub.
            return False
        now = self._now(eventtime)
        self.state = STATE_BOWDEN_FEED
        self._bowden_started_at = now
        self._bowden_deadline = now + self.config.bowden_timeout_s
        self._start_motor(self.config.bowden_speed_mm_s)
        self.counters["selections"] += 1
        self._log("info", "selection_confirmed", gate=self.active_gate, unit=self.active_unit)
        return True

    # -- Paso 6: buffer como splitter secundario ---------------------------
    def on_buffer_change(self) -> None:
        """Reajusta la velocidad del motor segun tension/compresion del buffer."""
        if self.state in (STATE_FEEDING, STATE_BOWDEN_FEED):
            base = self.config.feed_speed_mm_s if self.state == STATE_FEEDING else self.config.bowden_speed_mm_s
            self._start_motor(base)

    def buffer_state(self) -> str:
        return self.buffer.state()

    # -- Paso 3: validacion en tiempo real (impresion) ----------------------
    def monitor_print(self, moved_mm: float, diameter_mm: float = 0.0) -> List[str]:
        """Valida suministro y calidad durante la impresion.

        Devuelve la lista de incidencias detectadas (vacia si todo correcto).
        """
        issues: List[str] = []
        if self.hub.config.has_diameter_sensor and diameter_mm:
            if not (self.config.diameter_min_mm <= diameter_mm <= self.config.diameter_max_mm):
                issues.append(FAULT_DIAMETER_OUT_OF_RANGE)
        if self.hub.config.has_encoder and moved_mm < self.config.min_encoder_mm:
            issues.append(FAULT_NO_MOVEMENT)
        for issue in issues:
            if issue not in (self.fault_reason,):
                self.counters["faults"] += 1
                self._log("warning", issue, diameter_mm=diameter_mm, moved_mm=moved_mm)
        return issues

    # -- Ciclo de vida ------------------------------------------------------
    def _set_fault(self, reason: str, unit: Optional[str] = None, gate: Optional[int] = None) -> None:
        self._stop_motor()
        self._feed_deadline = 0.0
        self._bowden_deadline = 0.0
        self.fault_reason = reason
        self.state = STATE_FAULT
        self.counters["faults"] += 1
        self._log("error", "autoload_fault", reason=reason, unit=unit or self.active_unit, gate=gate or self.active_gate)

    def emergency_stop(self, reason: str = "emergency_stop") -> None:
        """Parada de emergencia idempotente: motor OFF y router liberado.

        No lanza excepciones (resiliencia absoluta): puede invocarse desde
        cualquier contexto, incluido un manejador de errores.
        """
        self._stop_motor()
        self._feed_deadline = 0.0
        self._bowden_deadline = 0.0
        if self.state not in (STATE_IDLE, STATE_FAULT):
            self.fault_reason = reason
            self.state = STATE_FAULT
            self.counters["faults"] += 1
        if self.active_unit:
            try:
                self.router.release(self.active_unit)
            except Exception:  # noqa: BLE001 - nunca propagar
                pass
            self.active_unit = None
        self._log("error", "emergency_stop", reason=reason)

    def reset(self) -> None:
        self._stop_motor()
        if self.active_unit:
            self.router.release(self.active_unit)
        self.state = STATE_IDLE
        self.active_gate = None
        self.active_unit = None
        self.fault_reason = FAULT_NONE
        self._feed_deadline = 0.0
        self._bowden_deadline = 0.0

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        if self.diagnostics is not None:
            self.diagnostics.log_event(level, "autoload", event, **kwargs)

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "gate": self.active_gate,
            "unit": self.active_unit,
            "fault": self.fault_reason,
            "motor_running": self._motor().running if self.active_unit else False,
            "next_deadline": self.next_deadline(),
            "hub": self.hub.as_dict(),
            "buffer": self.buffer.as_dict(),
            "router": self.router.as_dict(),
            "counters": dict(self.counters),
        }


# ---------------------------------------------------------------------------
# Servicio dirigido por el reactor de Klipper
# ---------------------------------------------------------------------------
class AutoLoadService:
    """Integra la maquina de estados con el **reactor de Klipper**.

    - Registra **un unico** ``reactor.register_timer`` (no bloquea el hilo
      principal; no hay *busy-wait* ni ``sleep``).
    - Despierta **solo** en el deadline util; en reposo devuelve
      ``reactor.NEVER`` (cero despertares, maximo rendimiento).
    - ``kick()`` re-arma el timer tras un evento de borde (p.ej. ``post_gate``)
      para reaccionar sin esperar al siguiente tick.
    - Aisla cualquier excepcion -> ``emergency_stop`` (resiliencia absoluta).

    El ``reactor`` se inyecta, por lo que el objeto sigue siendo testeable con
    un reactor simulado y usa la API real en runtime (``reactor.monotonic``,
    ``register_timer``, ``update_timer``, ``NEVER``).
    """

    def __init__(self, controller: AutoLoadController, reactor: Any, tick_s: float = 0.05) -> None:
        self.controller = controller
        self.reactor = reactor
        self.tick_s = max(0.0, float(tick_s))
        self._handle: Any = None
        self._active = False

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._handle = self.reactor.register_timer(self._timer, self.reactor.monotonic())

    def kick(self, eventtime: Optional[float] = None) -> None:
        """Re-arma el timer inmediatamente (llamado desde un handler de borde)."""
        if self._handle is None:
            return
        when = self.reactor.monotonic() if eventtime is None else float(eventtime)
        self.reactor.update_timer(self._handle, when)

    def _timer(self, eventtime: float) -> float:
        try:
            next_deadline = self.controller.service(eventtime)
        except Exception:  # noqa: BLE001 - un fallo nunca debe dejar el motor ON
            self.controller.emergency_stop("service_exception")
            next_deadline = None
        if not self._active or next_deadline is None:
            return self.reactor.NEVER
        return next_deadline

    def stop(self) -> None:
        self._active = False
        if self._handle is not None:
            self.reactor.update_timer(self._handle, self.reactor.NEVER)
        self.controller.emergency_stop("service_stop")

    @property
    def active(self) -> bool:
        return self._active


# ---------------------------------------------------------------------------
# Puente nativo con Klipper (buttons + reactor)
# ---------------------------------------------------------------------------
def wire_pre_gate_buttons(
    printer: Any,
    config: Any,
    controller: AutoLoadController,
    gate_pins: Dict[int, str],
    service: Optional["AutoLoadService"] = None,
) -> List[Any]:
    """Registra los sensores pre_gate con las **librerias propias de Klipper**.

    - ``printer.load_object(config, 'buttons')`` ->
      ``register_debounce_button`` (antirrebote por hardware/software de Klipper,
      sin polling propio).
    - ``printer.get_reactor().register_callback`` para **diferir** el trabajo: el
      handler del boton retorna de inmediato y el arranque del motor se ejecuta en
      el reactor (no bloquea el hilo principal).

    Devuelve la lista de ``(gate, pin, handle)`` registrados. En runtime se usa con
    objetos reales de Klipper; en pruebas, con dobles que replican su API.
    """
    buttons = printer.load_object(config, "buttons")
    reactor = printer.get_reactor()

    def _make_handler(gate: int):
        def _insert(eventtime: float) -> None:
            started = controller.on_pre_gate(gate, eventtime=eventtime)
            if started and service is not None:
                service.kick(eventtime)

        def _handler(eventtime: float, state: bool) -> None:
            if state:
                reactor.register_callback(_insert)

        return _handler

    handles: List[Any] = []
    for gate, pin in sorted(gate_pins.items()):
        handle = buttons.register_debounce_button(pin, _make_handler(int(gate)))
        handles.append((int(gate), pin, handle))
    return handles


__all__ = [
    "STATE_IDLE",
    "STATE_FEEDING",
    "STATE_WAITING_SELECTION",
    "STATE_BOWDEN_FEED",
    "STATE_READY",
    "STATE_FAULT",
    "FAULT_NONE",
    "FAULT_PRE_GATE_TIMEOUT",
    "FAULT_BOWDEN_TIMEOUT",
    "FAULT_DIAMETER_OUT_OF_RANGE",
    "FAULT_NO_MOVEMENT",
    "FAULT_BUFFER_CONFLICT",
    "BUFFER_NEUTRAL",
    "BUFFER_COMPRESSED",
    "BUFFER_EXPANDED",
    "BUFFER_DISABLED",
    "POST_GATE_HUB",
    "TOOLHEAD_SENSOR",
    "EXTRUDER_ENTRY_SENSOR",
    "BUFFER_TENSION",
    "BUFFER_COMPRESSION",
    "pre_gate_sensor",
    "post_gate_sensor",
    "MotorDriver",
    "NullMotor",
    "SensorBank",
    "HubConfig",
    "HubDevice",
    "BufferConfig",
    "BufferDevice",
    "MultiUnitRouter",
    "AutoLoadConfig",
    "AutoLoadController",
    "AutoLoadService",
    "wire_pre_gate_buttons",
]
