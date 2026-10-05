"""Adaptacion a Spoolman via Moonraker con reconciliacion bidireccional.

Implementa los patrones Adapter + Repository + Circuit Breaker: las consultas
salen por el cliente de Moonraker, con cache local y degradacion controlada
ante indisponibilidad del servidor (informe 6.12).

Requisitos: timeout < 5 s, reintentos idempotentes, coherencia tras reconexion.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SUPPORT_OFF = "off"
SUPPORT_READONLY = "readonly"
SUPPORT_PUSH = "push"
SUPPORT_PULL = "pull"
SUPPORT_MODES = (SUPPORT_OFF, SUPPORT_READONLY, SUPPORT_PUSH, SUPPORT_PULL)


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    """Lee un valor de un ConfigWrapper o de un dict de forma segura."""
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


@dataclass
class Spool:
    """Bobina de filamento."""

    spool_id: int
    material: str = ""
    color: str = ""
    name: str = ""
    remaining_g: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssignmentResult:
    """Resultado de asignar una bobina a un gate."""

    success: bool
    spool_id: int
    gate: int
    message: str = ""
    cached: bool = False


@dataclass
class InventoryStatus:
    """Resumen del inventario sincronizado."""

    online: bool
    spools: int = 0
    assigned_gates: int = 0
    last_sync: Optional[float] = None
    support: str = SUPPORT_PUSH

    def as_dict(self) -> Dict[str, Any]:
        return {
            "online": self.online,
            "spools": self.spools,
            "assigned_gates": self.assigned_gates,
            "last_sync": self.last_sync,
            "support": self.support,
        }


class _CircuitBreaker:
    """Disyuntor simple: abre tras N fallos y se recupera tras un cooldown."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if (time.monotonic() - self.opened_at) >= self.cooldown_s:
            self.opened_at = None
            self.failures = 0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


class SpoolManager:
    """Gestiona inventario y asignaciones de bobinas."""

    def __init__(self, moonraker_client: Any, config: Any = None) -> None:
        self.client = moonraker_client
        self.config = config
        self.breaker = _CircuitBreaker()
        self.gate_map: Dict[int, int] = {}  # gate -> spool_id
        self._cache: Dict[int, Spool] = {}
        self._tags: Dict[str, int] = {}  # uid -> spool_id
        self.last_sync: Optional[float] = None
        self.base_url = self._base_url(config)
        self.support = str(_cfg_get(config, "spoolman_support", SUPPORT_PUSH)).lower()
        if self.support not in SUPPORT_MODES:
            self.support = SUPPORT_PUSH

    # -- Modos --------------------------------------------------------------
    @property
    def is_read_enabled(self) -> bool:
        return self.support != SUPPORT_OFF

    @property
    def is_write_enabled(self) -> bool:
        return self.support in (SUPPORT_PUSH, SUPPORT_PULL)

    @property
    def should_pull(self) -> bool:
        return self.support == SUPPORT_PULL

    def set_support(self, mode: str) -> str:
        """Cambia el modo (off/readonly/push/pull) y lo devuelve normalizado."""
        normalized = str(mode).lower()
        if normalized in SUPPORT_MODES:
            self.support = normalized
        return self.support

    @staticmethod
    def _base_url(config: Any) -> str:
        default = "http://127.0.0.1:7125"
        if config is None:
            return default
        if isinstance(config, dict):
            return str(config.get("spoolman_url", default))
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return str(getter("spoolman_url", default))
            except TypeError:
                return default
        return default

    # -- Infraestructura HTTP ----------------------------------------------
    def _request(self, path: str, method: str = "GET", payload: Optional[dict] = None) -> Optional[Any]:
        if self.client is None and not self.base_url:
            return None
        if self.breaker.is_open:
            return None
        url = f"{self.base_url.rstrip('/')}/server/spoolman/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - URL local
                body = response.read().decode("utf-8")
            self.breaker.record_success()
            return json.loads(body) if body else {}
        except (urllib.error.URLError, OSError, ValueError):
            self.breaker.record_failure()
            return None

    # -- API publica --------------------------------------------------------
    def get_spool(self, spool_id: int) -> Optional[Spool]:
        if not self.is_read_enabled:
            return self._cache.get(spool_id)
        data = self._request(f"v1/spool/{spool_id}")
        if data is None:
            return self._cache.get(spool_id)
        spool = Spool(
            spool_id=int(data.get("id", spool_id)),
            material=str(data.get("filament", {}).get("material", "")),
            color=str(data.get("filament", {}).get("color_hex", "")),
            name=str(data.get("filament", {}).get("name", "")),
            remaining_g=data.get("remaining_weight"),
        )
        self._cache[spool.spool_id] = spool
        return spool

    def assign_spool_to_gate(self, spool_id: int, gate: int, force: bool = False) -> AssignmentResult:
        # Validacion de conflicto: un spool no puede estar en dos gates.
        for existing_gate, assigned in self.gate_map.items():
            if assigned == spool_id and existing_gate != gate and not force:
                return AssignmentResult(
                    False, spool_id, gate, f"spool {spool_id} ya asignado al gate {existing_gate}"
                )
        self.gate_map[gate] = spool_id
        if not self.is_write_enabled:
            return AssignmentResult(True, spool_id, gate, "asignacion local (modo sin escritura)", cached=True)
        payload = {"spool_id": spool_id, "location": f"gate_{gate}"}
        response = self._request("v1/spool", method="PUT", payload=payload)
        cached = response is None
        return AssignmentResult(True, spool_id, gate, "asignacion registrada", cached=cached)

    def sync_gate_map(self) -> bool:
        if not self.is_read_enabled:
            return False
        data = self._request("v1/spool")
        if data is None:
            return False
        spools = data if isinstance(data, list) else data.get("result", [])
        self._cache = {
            int(item["id"]): Spool(spool_id=int(item["id"])) for item in spools if "id" in item
        }
        self.last_sync = time.time()
        return True

    def update_spool_consumption(self, spool_id: int, consumed_len: float) -> bool:
        if not self.is_write_enabled:
            return False
        response = self._request(
            "v1/spool", method="PUT", payload={"spool_id": spool_id, "consumed_length": consumed_len}
        )
        return response is not None

    def notify_toolchange(self, gate: int, spool_id: int, printer_name: str = "") -> bool:
        """Notificar a Spoolman sobre un cambio de herramienta."""
        if not self.is_write_enabled or self.breaker.is_open:
            return False
        payload = {
            "printer_name": printer_name,
            "gate": gate,
            "spool_id": spool_id,
            "action": "toolchange",
        }
        response = self._request("v1/toolchange", method="POST", payload=payload)
        return response is not None

    # -- Tags NFC -----------------------------------------------------------
    def register_tag(self, uid: str, spool_id: int) -> None:
        """Registra la asociacion tag NFC -> bobina (MMU_SPOOLMAN TAG=)."""
        self._tags[str(uid)] = int(spool_id)

    def resolve_tag(self, uid: str) -> Optional[int]:
        return self._tags.get(str(uid))

    def handle_nfc_tag(self, uid: str) -> Optional[Spool]:
        """Reconcilia un tag NFC con el inventario (DM-NFC-001)."""
        spool_id = self._tags.get(str(uid))
        if spool_id is not None:
            spool = self.get_spool(spool_id)
            if spool is not None:
                return spool
        for spool in self._cache.values():
            if spool.extra.get("nfc_uid") == uid:
                return spool
        return None

    def pull_gate_map(self, gates: int) -> Dict[int, int]:
        """Modo pull: lee las ubicaciones ``gate_N`` de Spoolman al mapa local."""
        if not self.should_pull:
            return dict(self.gate_map)
        data = self._request("v1/spool")
        if data is None:
            return dict(self.gate_map)
        spools = data if isinstance(data, list) else data.get("result", [])
        for item in spools:
            location = str(item.get("location", ""))
            if location.startswith("gate_"):
                try:
                    gate = int(location.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if 0 <= gate < gates and "id" in item:
                    self.gate_map[gate] = int(item["id"])
        return dict(self.gate_map)

    def get_tag_map(self) -> Dict[str, int]:
        return dict(self._tags)

    def get_inventory_status(self) -> InventoryStatus:
        return InventoryStatus(
            online=not self.breaker.is_open,
            spools=len(self._cache),
            assigned_gates=len(self.gate_map),
            last_sync=self.last_sync,
            support=self.support,
        )


__all__ = [
    "SpoolManager", "Spool", "AssignmentResult", "InventoryStatus",
    "SUPPORT_OFF", "SUPPORT_READONLY", "SUPPORT_PUSH", "SUPPORT_PULL",
]
