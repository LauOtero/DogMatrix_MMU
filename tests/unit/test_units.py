"""Pruebas del sistema multi-unidad (scanners, asignacion, persistencia)."""

from __future__ import annotations

from dog_matrix.units import (
    EVENT_ASSIGNED,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    CanDeviceScanner,
    CompositeScanner,
    DeviceScanner,
    DiscoveredDevice,
    DogMatrixController,
    UnitAssignmentStore,
    UsbDeviceScanner,
)


class MutableScanner(DeviceScanner):
    """Scanner controlable para pruebas (simula conexion/desconexion)."""

    interface = "usb"

    def __init__(self, devices=None):
        self.devices = list(devices or [])

    def scan(self):
        return list(self.devices)

    def set_devices(self, ids):
        self.devices = [DiscoveredDevice(device_id=i, interface="usb", address=f"/dev/{i}") for i in ids]


def _controller(tmp_path, scanner, **kwargs):
    return DogMatrixController(
        scanners=[scanner],
        store_path=str(tmp_path / "units.json"),
        **kwargs,
    )


# --- Scanners ---------------------------------------------------------------
def test_usb_scanner_lists_matching_only(tmp_path):
    (tmp_path / "usb-DogMatrix_MMU-1-if00").write_text("")
    (tmp_path / "usb-Other_Device-if00").write_text("")
    scanner = UsbDeviceScanner(scan_dir=str(tmp_path), pattern="DogMatrix")
    devices = scanner.scan()
    assert len(devices) == 1
    assert devices[0].device_id == "usb:usb-DogMatrix_MMU-1-if00"


def test_usb_scanner_missing_dir_returns_empty():
    scanner = UsbDeviceScanner(scan_dir="this/dir/does/not/exist")
    assert scanner.scan() == []


def test_usb_scanner_disabled_returns_empty(tmp_path):
    (tmp_path / "usb-DogMatrix-if00").write_text("")
    assert UsbDeviceScanner(scan_dir=str(tmp_path), enabled=False).scan() == []


def test_can_scanner_uses_provider():
    scanner = CanDeviceScanner(uuid_provider=lambda: ["abc123", "def456"])
    devices = scanner.scan()
    assert [d.device_id for d in devices] == ["can:abc123", "can:def456"]


def test_can_scanner_without_provider_returns_empty():
    assert CanDeviceScanner(uuid_provider=None).scan() == []


def test_composite_scanner_deduplicates():
    a = MutableScanner()
    a.set_devices(["usb:1"])
    b = MutableScanner()
    b.set_devices(["usb:1", "usb:2"])
    devices = CompositeScanner([a, b]).scan()
    assert [d.device_id for d in devices] == ["usb:1", "usb:2"]


# --- Asignacion secuencial --------------------------------------------------
def test_sequential_assignment_follows_connection_order(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:first", "usb:second", "usb:third"])
    controller.poll()
    numbers = {u.unit_id: u.spool_number for u in controller.list_units()}
    assert numbers == {"usb:first": 0, "usb:second": 1, "usb:third": 2}


def test_disconnect_keeps_assignment_and_reconnect_restores(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a", "usb:b"])
    controller.poll()

    scanner.set_devices(["usb:b"])  # 'usb:a' se desconecta
    controller.poll()
    units = {u.unit_id: u for u in controller.list_units()}
    assert units["usb:a"].connected is False  # se conserva como conocida, desconectada
    assert units["usb:a"].spool_number == 0

    scanner.set_devices(["usb:b", "usb:a"])  # reconexion
    controller.poll()
    units = {u.unit_id: u for u in controller.list_units()}
    assert units["usb:a"].spool_number == 0  # conserva su numero original
    assert units["usb:b"].spool_number == 1
    assert units["usb:a"].connected is True


def test_new_device_gets_next_sequential_number_after_existing(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a"])
    controller.poll()
    scanner.set_devices(["usb:a", "usb:c"])
    controller.poll()
    numbers = {u.unit_id: u.spool_number for u in controller.list_units()}
    assert numbers["usb:c"] == 1


def test_gate_offsets_recomputed_by_spool_number(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a", "usb:b"])
    controller.poll()
    units = {u.unit_id: u for u in controller.list_units()}
    assert units["usb:a"].gate_offset == 0
    assert units["usb:b"].gate_offset == 4
    assert units["usb:b"].global_gate(0) == 4
    assert units["usb:b"].owns_global_gate(5) is True


# --- Persistencia -----------------------------------------------------------
def test_persistence_across_restart(tmp_path):
    scanner = MutableScanner()
    store_path = str(tmp_path / "units.json")

    controller1 = DogMatrixController(scanners=[scanner], store_path=store_path, default_gates=4)
    scanner.set_devices(["usb:a", "usb:b"])
    controller1.poll()

    # "Reinicio": nuevo controller con el mismo almacen, sin dispositivos aun.
    controller2 = DogMatrixController(
        scanners=[MutableScanner()], store_path=store_path, default_gates=4
    )
    assert controller2.store.get("usb:a") == 0
    assert controller2.store.get("usb:b") == 1


def test_manual_override_takes_precedence(tmp_path):
    store_path = str(tmp_path / "units.json")
    store = UnitAssignmentStore(store_path)
    store.assign("usb:a", 0)
    # Override manual: usb:b debe ocupar el numero 7.
    store2 = UnitAssignmentStore(store_path, manual_assignments={"usb:b": 7})
    assert store2.get("usb:b") == 7
    # El override se persiste para futuros arranques.
    store3 = UnitAssignmentStore(store_path)
    assert store3.get("usb:b") == 7


def test_clear_assignments_reassigns_sequentially(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a", "usb:b"])
    controller.poll()
    controller.assign_spool("usb:a", 9)
    assert controller.store.get("usb:a") == 9

    controller.clear_assignments()
    numbers = {u.unit_id: u.spool_number for u in controller.list_units()}
    assert numbers == {"usb:a": 0, "usb:b": 1}


# --- Eventos ----------------------------------------------------------------
def test_events_emitted_on_connect_disconnect(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner)
    received = []
    controller.register_listener(received.append)

    scanner.set_devices(["usb:a"])
    controller.poll()
    types = [e.event_type for e in received]
    assert EVENT_CONNECTED in types
    assert EVENT_ASSIGNED in types

    received.clear()
    scanner.set_devices([])
    controller.poll()
    assert [e.event_type for e in received] == [EVENT_DISCONNECTED]


# --- Arbitraje del toolhead -------------------------------------------------
def test_toolhead_arbitration_exclusive(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a", "usb:b"])
    controller.poll()

    assert controller.acquire_toolhead("usb:a") is True
    assert controller.acquire_toolhead("usb:b") is False  # ocupado por usb:a
    controller.release_toolhead("usb:a")
    assert controller.acquire_toolhead("usb:b") is True


def test_get_status_reports_assignments(tmp_path):
    scanner = MutableScanner()
    controller = _controller(tmp_path, scanner, default_gates=4)
    scanner.set_devices(["usb:a"])
    controller.poll()
    status = controller.get_status()
    assert status["enabled"] is True
    assert status["count"] == 1
    assert status["assignments"] == {"usb:a": 0}
