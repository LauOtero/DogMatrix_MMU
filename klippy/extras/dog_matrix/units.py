"""Sistema multi-unidad: descubrimiento, asignacion y coordinacion de MMUs.

Este modulo implementa la capa multi-unidad de Dog Matrix MMU:

- ``DeviceScanner`` / ``UsbDeviceScanner`` / ``CanDeviceScanner``: deteccion de
  dispositivos por USB (``/dev/serial/by-id``) y CANbus (UUID), con fuentes
  inyectables para pruebas sin hardware.
- ``DeviceDiscovery``: escaneo continuo, seguimiento de conexion/desconexion y
  emision de eventos.
- ``UnitAssignmentStore``: persistencia permanente (atomica + checksum) de la
  asignacion dispositivo -> numero de bobina. Solo cambia con un borrado
  completo o con una asignacion manual explicita.
- ``DogMatrixUnit``: modelo de una unidad (bobina) con su estado y su bloque de
  gates globales.
- ``DogMatrixController``: orquesta descubrimiento, asignacion, arbitraje del
  toolhead compartido y exposicion de estado.

Diseno seguro ante fallos: si no hay hardware accesible, los scanners devuelven
listas vacias y el sistema permanece estable.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from .boards import COILS_PER_UNIT
from .persistence import Persistence, PersistenceError

UNIT_SCHEMA_VERSION = 1

# Tipos de evento de descubrimiento.
EVENT_CONNECTED = "connected"
EVENT_DISCONNECTED = "disconnected"
EVENT_ASSIGNED = "assigned"

INTERFACE_USB = "usb"
INTERFACE_CAN = "can"


# ---------------------------------------------------------------------------
# Dispositivos y scanners
# ---------------------------------------------------------------------------
@dataclass
class DiscoveredDevice:
    """Dispositivo fisico detectado por un scanner."""

    device_id: str
    interface: str
    address: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"device_id": self.device_id, "interface": self.interface, "address": self.address}


class DeviceScanner:
    """Contrato comun de los scanners de dispositivos."""

    interface = "generic"

    def scan(self) -> List[DiscoveredDevice]:
        raise NotImplementedError


class UsbDeviceScanner(DeviceScanner):
    """Detecta MMUs por USB listando enlaces de ``/dev/serial/by-id``."""

    interface = INTERFACE_USB

    def __init__(
        self,
        scan_dir: str = "/dev/serial/by-id",
        pattern: str = "DogMatrix",
        enabled: bool = True,
    ) -> None:
        self.scan_dir = scan_dir
        self.pattern = pattern.lower()
        self.enabled = enabled

    def scan(self) -> List[DiscoveredDevice]:
        if not self.enabled:
            return []
        try:
            names = sorted(os.listdir(self.scan_dir))
        except OSError:
            return []
        devices: List[DiscoveredDevice] = []
        for name in names:
            if self.pattern and self.pattern not in name.lower():
                continue
            devices.append(
                DiscoveredDevice(
                    device_id=f"{INTERFACE_USB}:{name}",
                    interface=INTERFACE_USB,
                    address=os.path.join(self.scan_dir, name),
                )
            )
        return devices


class CanDeviceScanner(DeviceScanner):
    """Detecta MMUs por CANbus a partir de un proveedor de UUIDs inyectable."""

    interface = INTERFACE_CAN

    def __init__(
        self,
        uuid_provider: Optional[Callable[[], Iterable[str]]] = None,
        enabled: bool = True,
    ) -> None:
        self.uuid_provider = uuid_provider
        self.enabled = enabled

    def scan(self) -> List[DiscoveredDevice]:
        if not self.enabled or self.uuid_provider is None:
            return []
        try:
            uuids = list(self.uuid_provider())
        except Exception:  # noqa: BLE001 - proveedor no disponible
            return []
        return [
            DiscoveredDevice(device_id=f"{INTERFACE_CAN}:{uuid}", interface=INTERFACE_CAN, address=str(uuid))
            for uuid in sorted(str(u) for u in uuids)
        ]


class CompositeScanner(DeviceScanner):
    """Combina varios scanners eliminando duplicados por ``device_id``."""

    def __init__(self, scanners: Iterable[DeviceScanner]) -> None:
        self.scanners = list(scanners)

    def scan(self) -> List[DiscoveredDevice]:
        result: List[DiscoveredDevice] = []
        seen = set()
        for scanner in self.scanners:
            for device in scanner.scan():
                if device.device_id in seen:
                    continue
                seen.add(device.device_id)
                result.append(device)
        return result


@dataclass
class UnitEvent:
    """Evento de conexion/desconexion/asignacion de una unidad."""

    event_type: str
    device_id: str
    spool_number: Optional[int] = None
    timestamp: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "device_id": self.device_id,
            "spool_number": self.spool_number,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Persistencia de la asignacion
# ---------------------------------------------------------------------------
class UnitAssignmentStore:
    """Persiste de forma permanente la asignacion dispositivo -> numero de bobina.

    El almacen solo se modifica mediante ``assign``/``clear``; la ausencia
    temporal de un dispositivo (desconexion) nunca altera su numero asignado.
    """

    def __init__(self, path: str, manual_assignments: Optional[Dict[str, int]] = None) -> None:
        self._persistence = Persistence(path)
        self.assignments: Dict[str, int] = {}
        self._manual = {str(k): int(v) for k, v in (manual_assignments or {}).items()}
        self._load()

    def _load(self) -> None:
        try:
            data = self._persistence.load()
        except PersistenceError:
            data = {}
        stored = data.get("assignments") if isinstance(data, dict) else None
        if isinstance(stored, dict):
            self.assignments = {str(k): int(v) for k, v in stored.items() if isinstance(v, (int, float))}
        # La asignacion manual tiene prioridad sobre la persistida.
        for device_id, spool_number in self._manual.items():
            self.assignments[device_id] = spool_number
        if self._manual:
            self._save()

    def _save(self) -> None:
        self._persistence.save({"schema_version": UNIT_SCHEMA_VERSION, "assignments": self.assignments})

    def get(self, device_id: str) -> Optional[int]:
        return self.assignments.get(device_id)

    def next_spool_number(self) -> int:
        return max(self.assignments.values(), default=-1) + 1

    def assign(self, device_id: str, spool_number: int, persist: bool = True) -> None:
        self.assignments[device_id] = int(spool_number)
        if persist:
            self._save()

    def ensure(self, device_id: str) -> int:
        """Devuelve el numero asignado, creando uno secuencial si es nuevo."""
        existing = self.assignments.get(device_id)
        if existing is not None:
            return existing
        spool_number = self.next_spool_number()
        self.assign(device_id, spool_number)
        return spool_number

    def clear(self) -> None:
        """Borrado completo de la configuracion de asignaciones."""
        self.assignments = {}
        self._save()

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.assignments)


# ---------------------------------------------------------------------------
# Modelo de unidad
# ---------------------------------------------------------------------------
@dataclass
class DogMatrixUnit:
    """Una unidad (bobina) del sistema multi-unidad.

    Cada placa base Dog Matrix controla exactamente ``coils_per_unit`` bobinas
    (por defecto 4). Las placas selectoras (ERCF) declaran un numero de gates
    dependiente de la expansion.
    """

    unit_id: str
    spool_number: int
    interface: str = INTERFACE_USB
    address: str = ""
    gates: int = 0
    gate_offset: int = 0
    coils_per_unit: int = COILS_PER_UNIT
    connected: bool = False
    last_seen: Optional[float] = None
    # Estado dinamico por unidad.
    current_gate: Optional[int] = None
    current_tool: Optional[int] = None
    ttg_map: List[int] = field(default_factory=list)
    gate_filament: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.gates and self.coils_per_unit > 1:
            # Placa Dog Matrix gear-per-gate: 4 bobinas por unidad.
            self.gates = self.coils_per_unit
        if not self.ttg_map and self.gates:
            self.ttg_map = list(range(self.gates))
        if not self.gate_filament and self.gates:
            self.gate_filament = [
                {"material": "", "color": "", "spool_id": "", "availability": "unknown"}
                for _ in range(self.gates)
            ]

    # -- Mapeo local <-> global --------------------------------------------
    def global_gate(self, local_gate: int) -> int:
        return self.gate_offset + local_gate

    def local_gate(self, global_gate: int) -> int:
        return global_gate - self.gate_offset

    def owns_global_gate(self, global_gate: int) -> bool:
        return self.gate_offset <= global_gate < self.gate_offset + self.gates

    def as_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "spool_number": self.spool_number,
            "interface": self.interface,
            "address": self.address,
            "gates": self.gates,
            "gate_offset": self.gate_offset,
            "coils_per_unit": self.coils_per_unit,
            "connected": self.connected,
            "last_seen": self.last_seen,
            "current_gate": self.current_gate,
            "current_tool": self.current_tool,
            "ttg_map": list(self.ttg_map),
            "gate_filament": [dict(item) for item in self.gate_filament],
        }


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------
class DeviceDiscovery:
    """Escaneo continuo de dispositivos con emision de eventos connect/disconnect."""

    def __init__(
        self,
        scanner: DeviceScanner,
        store: UnitAssignmentStore,
        diagnostics: Any = None,
        clock: Callable[[], float] = time.time,
        default_gates: int = 0,
        coils_per_unit: int = COILS_PER_UNIT,
    ) -> None:
        self.scanner = scanner
        self.store = store
        self.diagnostics = diagnostics
        self._clock = clock
        self.default_gates = default_gates
        self.coils_per_unit = max(1, int(coils_per_unit))
        self.units: Dict[str, DogMatrixUnit] = {}
        self.known_devices: Dict[str, DiscoveredDevice] = {}

    # -- API ----------------------------------------------------------------
    def poll(self) -> List[UnitEvent]:
        """Escanea, reconcilia el estado y devuelve la lista de eventos."""
        events: List[UnitEvent] = []
        discovered = self.scanner.scan()
        present_ids = set()

        for device in discovered:
            present_ids.add(device.device_id)
            if device.device_id not in self.known_devices:
                self.known_devices[device.device_id] = device
                events.extend(self._on_connect(device))
            else:
                unit = self.units.get(device.device_id)
                if unit is not None:
                    unit.last_seen = self._clock()

        for device_id in list(self.known_devices.keys()):
            if device_id not in present_ids:
                events.extend(self._on_disconnect(device_id))

        return events

    def _on_connect(self, device: DiscoveredDevice) -> List[UnitEvent]:
        now = self._clock()
        spool_number = self.store.ensure(device.device_id)
        gates = self.default_gates if self.default_gates > 0 else (
            self.coils_per_unit if self.coils_per_unit > 1 else 0
        )
        unit = DogMatrixUnit(
            unit_id=device.device_id,
            spool_number=spool_number,
            interface=device.interface,
            address=device.address,
            gates=gates,
            coils_per_unit=self.coils_per_unit,
            connected=True,
            last_seen=now,
        )
        self.units[device.device_id] = unit
        self._log("info", "unit_connected", device_id=device.device_id, spool_number=spool_number)
        return [
            UnitEvent(EVENT_CONNECTED, device.device_id, spool_number, now),
            UnitEvent(EVENT_ASSIGNED, device.device_id, spool_number, now),
        ]

    def _on_disconnect(self, device_id: str) -> List[UnitEvent]:
        device = self.known_devices.pop(device_id)
        unit = self.units.get(device_id)
        if unit is not None:
            unit.connected = False
        self._log("warning", "unit_disconnected", device_id=device_id)
        return [UnitEvent(EVENT_DISCONNECTED, device_id, unit.spool_number if unit else None, self._clock())]

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        if self.diagnostics is not None:
            self.diagnostics.log_event(level, "units", event, **kwargs)

    def list_units(self) -> List[DogMatrixUnit]:
        return sorted(self.units.values(), key=lambda unit: unit.spool_number)

    def get_unit(self, device_id: str) -> Optional[DogMatrixUnit]:
        return self.units.get(device_id)


# ---------------------------------------------------------------------------
# Controlador multi-unidad
# ---------------------------------------------------------------------------
class DogMatrixController:
    """Coordina multiples unidades: descubrimiento, asignacion y arbitraje."""

    def __init__(
        self,
        scanners: Optional[Iterable[DeviceScanner]] = None,
        store_path: Optional[str] = None,
        manual_assignments: Optional[Dict[str, int]] = None,
        default_gates: int = 0,
        coils_per_unit: int = COILS_PER_UNIT,
        scan_interval_s: float = 5.0,
        diagnostics: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.diagnostics = diagnostics
        self.scan_interval_s = max(0.1, float(scan_interval_s))
        self._clock = clock
        self._listeners: List[Callable[[UnitEvent], None]] = []
        self._reactor: Any = None
        self._timer_active = False
        self.active_unit_id: Optional[str] = None
        self._toolhead_owner: Optional[str] = None

        scanner_list = list(scanners) if scanners is not None else []
        self.scanner = CompositeScanner(scanner_list)
        resolved_path = store_path or os.path.join(tempfile.gettempdir(), "dog_matrix_units.json")
        self.store = UnitAssignmentStore(resolved_path, manual_assignments=manual_assignments)
        self.discovery = DeviceDiscovery(
            scanner=self.scanner,
            store=self.store,
            diagnostics=diagnostics,
            clock=clock,
            default_gates=default_gates,
            coils_per_unit=coils_per_unit,
        )

    # -- Listeners ----------------------------------------------------------
    def register_listener(self, callback: Callable[[UnitEvent], None]) -> None:
        self._listeners.append(callback)

    def _emit(self, event: UnitEvent) -> None:
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - aislar fallos de listener
                continue

    # -- Descubrimiento -----------------------------------------------------
    def poll(self) -> List[UnitEvent]:
        events = self.discovery.poll()
        self._recompute_offsets()
        for event in events:
            self._emit(event)
        return events

    def _recompute_offsets(self) -> None:
        offset = 0
        for unit in self.discovery.list_units():
            unit.gate_offset = offset
            offset += unit.gates

    def start(self, reactor: Any = None, interval_s: Optional[float] = None) -> None:
        """Arranca el escaneo continuo sobre el reactor de Klipper (si existe)."""
        if reactor is None or self._timer_active:
            return
        if not (hasattr(reactor, "register_timer") and hasattr(reactor, "monotonic")):
            return
        self._reactor = reactor
        self._timer_active = True
        period = interval_s if interval_s is not None else self.scan_interval_s
        reactor.register_timer(self._on_timer, reactor.monotonic() + period)

    def _on_timer(self, eventtime: float) -> float:
        self.poll()
        return eventtime + self.scan_interval_s

    def stop(self) -> None:
        self._timer_active = False
        self._reactor = None

    # -- Asignacion ---------------------------------------------------------
    def assign_spool(self, device_id: str, spool_number: int) -> DogMatrixUnit:
        """Asignacion manual (usuario) del numero de bobina."""
        self.store.assign(device_id, spool_number)
        unit = self.discovery.get_unit(device_id)
        if unit is None:
            unit = DogMatrixUnit(
                unit_id=device_id,
                spool_number=spool_number,
                gates=0,
                coils_per_unit=self.discovery.coils_per_unit,
            )
            self.discovery.units[device_id] = unit
        else:
            unit.spool_number = spool_number
        self._recompute_offsets()
        return unit

    def clear_assignments(self) -> None:
        """Borrado completo de la configuracion de asignaciones."""
        self.store.clear()
        for unit in sorted(self.discovery.units.values(), key=lambda item: item.unit_id):
            unit.spool_number = self.store.ensure(unit.unit_id)
        self._recompute_offsets()

    # -- Arbitraje del toolhead compartido ---------------------------------
    def acquire_toolhead(self, unit_id: str) -> bool:
        """Solicita acceso exclusivo al toolhead compartido."""
        if self._toolhead_owner is None or self._toolhead_owner == unit_id:
            self._toolhead_owner = unit_id
            self.active_unit_id = unit_id
            return True
        return False

    def release_toolhead(self, unit_id: str) -> None:
        if self._toolhead_owner == unit_id:
            self._toolhead_owner = None

    def select_unit(self, unit_id: str) -> bool:
        if unit_id in self.discovery.units:
            self.active_unit_id = unit_id
            return True
        return False

    # -- Estado -------------------------------------------------------------
    def list_units(self) -> List[DogMatrixUnit]:
        return self.discovery.list_units()

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "active_unit": self.active_unit_id,
            "toolhead_owner": self._toolhead_owner,
            "count": len(self.discovery.units),
            "assignments": self.store.as_dict(),
            "units": [unit.as_dict() for unit in self.discovery.list_units()],
        }


def build_default_controller(config: Any, profile: Any, store_path: str, diagnostics: Any) -> DogMatrixController:
    """Construye un controller a partir de la configuracion de Klipper.

    Respeta las claves ``usb_scan_dir``, ``usb_scan_pattern``, ``can_uuids``,
    ``unit_assignments``, ``unit_scan_interval_s`` y ``num_gates``.
    """
    def _get(key: str, default: Any) -> Any:
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

    usb_scanner = UsbDeviceScanner(
        scan_dir=str(_get("usb_scan_dir", "/dev/serial/by-id")),
        pattern=str(_get("usb_scan_pattern", "DogMatrix")),
        enabled=bool(_get("enable_usb_scan", True)),
    )
    can_uuids = _get("can_uuids", None)
    can_scanner = CanDeviceScanner(
        uuid_provider=(lambda: can_uuids) if can_uuids else None,
        enabled=bool(_get("enable_can_scan", True)),
    )
    manual = _get("unit_assignments", None)
    if isinstance(manual, str):
        try:
            import json

            manual = json.loads(manual)
        except (ValueError, TypeError):
            manual = None
    coils_per_unit = int(_get("coils_per_unit", COILS_PER_UNIT))
    # Cada unidad Dog Matrix (gear-per-gate) controla 4 bobinas; una placa
    # selectora (ERCF) declara sus gates desde el perfil.
    num_gates = _get("num_gates", None)
    if num_gates is None:
        topology = getattr(profile, "topology_type", "")
        num_gates = (getattr(profile, "gates", 0) or 0) if topology == "selector" else 0
    return DogMatrixController(
        scanners=[usb_scanner, can_scanner],
        store_path=store_path,
        manual_assignments=manual if isinstance(manual, dict) else None,
        default_gates=int(num_gates or 0),
        coils_per_unit=coils_per_unit,
        scan_interval_s=float(_get("unit_scan_interval_s", 5.0)),
        diagnostics=diagnostics,
    )


__all__ = [
    "UNIT_SCHEMA_VERSION",
    "EVENT_CONNECTED",
    "EVENT_DISCONNECTED",
    "EVENT_ASSIGNED",
    "INTERFACE_USB",
    "INTERFACE_CAN",
    "DiscoveredDevice",
    "DeviceScanner",
    "UsbDeviceScanner",
    "CanDeviceScanner",
    "CompositeScanner",
    "UnitEvent",
    "UnitAssignmentStore",
    "DogMatrixUnit",
    "DeviceDiscovery",
    "DogMatrixController",
    "build_default_controller",
]
