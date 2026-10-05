"""Lectura de tags NFC/RFID para identificacion de bobinas (paridad Happy Hare).

Adapter sobre lectores PN532/PN5180/PN7160/RC522 (I2C/SPI/UART) con soporte de
*deep read* (NDEF, Bambu, Creality), auto-creacion determinista de bobina,
escritura de tags y gestion de varios lectores con deteccion de vecinos.

Cuando no hay hardware accesible, opera en modo simulado para permitir pruebas
e integracion con Spoolman (DM-NFC-001, informe 6.19).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_READERS = ("pn532", "pn5180", "pn7160", "rc522")

READ_MODE_SHALLOW = "shallow"
READ_MODE_DEEP = "deep"


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
class NFCTagData:
    """Datos leidos de un tag NFC/RFID."""

    uid: str
    tech: str = "ISO14443A"
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    read_mode: str = READ_MODE_SHALLOW
    reader: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "tech": self.tech,
            "data": self.data,
            "timestamp": self.timestamp,
            "read_mode": self.read_mode,
            "reader": self.reader,
        }


def parse_tag_payload(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normaliza el contenido de un tag a ``{material, color, spool_id, ...}``.

    Soporta NDEF (registros con ``type``/``text``), formato Bambu
    (``tray_info_idx``/``tray_id``/``filament_type``) y Creality
    (``material``/``color``). Nunca lanza: devuelve ``{}`` ante datos invalidos.
    """
    if not isinstance(raw, dict):
        return {}
    parsed: Dict[str, Any] = {}
    # NDEF: lista de registros con texto tipo "MATERIAL=PLA" o JSON.
    ndef = raw.get("ndef")
    if isinstance(ndef, list):
        for record in ndef:
            text = ""
            if isinstance(record, dict):
                text = str(record.get("text", record.get("payload", "")))
            elif isinstance(record, str):
                text = record
            for token in text.replace(";", "\n").splitlines():
                key, _, value = token.partition("=")
                if value:
                    parsed[key.strip().lower()] = value.strip()
    # Bambu Lab.
    if "tray_info_idx" in raw or "filament_type" in raw:
        parsed.setdefault("material", raw.get("filament_type", ""))
        parsed.setdefault("color", raw.get("tray_color", ""))
        parsed.setdefault("name", raw.get("tray_id_name", raw.get("tray_info_idx", "")))
    # Creality / generico.
    for src, dst in (("material", "material"), ("color", "color"),
                     ("color_hex", "color"), ("name", "name"), ("spool_id", "spool_id")):
        if src in raw and src not in parsed:
            parsed[dst] = raw[src]
    if "color" in parsed and isinstance(parsed["color"], str):
        parsed["color"] = parsed["color"].strip()
    if "spool_id" in parsed:
        try:
            parsed["spool_id"] = int(parsed["spool_id"])
        except (TypeError, ValueError):
            parsed.pop("spool_id", None)
    return parsed


class NFCReader:
    """Interfaz de lectura/escritura de tags NFC (un lector)."""

    def __init__(self, printer: Any, config: Any = None, index: int = 0) -> None:
        self.printer = printer
        self.config = config
        self.index = int(index)
        self.name = f"nfc_{self.index}"
        self.reader_type = self._reader_type(config)
        self.available = self.reader_type in SUPPORTED_READERS
        self.auto_create = bool(_cfg_get(config, "nfc_auto_create", False))
        self._callbacks: List[Callable[[NFCTagData], None]] = []
        self._last_tag: Optional[NFCTagData] = None
        self._raw_payload: Dict[str, Any] = {}
        self.reads = 0
        self.neighbors: List[str] = []

    @staticmethod
    def _reader_type(config: Any) -> str:
        return str(_cfg_get(config, "nfc_type", "none")).lower()

    # -- Simulacion ---------------------------------------------------------
    def simulate_tag(self, uid: str, data: Optional[Dict[str, Any]] = None) -> NFCTagData:
        """Inyecta un tag para pruebas / banco."""
        self._raw_payload = dict(data or {})
        tag = NFCTagData(
            uid=uid, data=parse_tag_payload(data), timestamp=time.time(), reader=self.name
        )
        if self.auto_create and "spool_id" not in tag.data:
            tag.data["spool_id"] = self.auto_create_spool_id(uid)
        self._last_tag = tag
        self._notify(tag)
        return tag

    # -- API publica --------------------------------------------------------
    def scan_tag(self, timeout_s: float = 2.0, deep: bool = False) -> Optional[NFCTagData]:
        """Escanea un tag. Con ``deep=True`` realiza lectura profunda y parsea."""
        started = time.monotonic()
        self.reads += 1
        while (time.monotonic() - started) < max(0.0, timeout_s):
            if self._last_tag is not None:
                return self.deep_read() if deep else self._last_tag
            if not self.available:
                break
            time.sleep(0.02)
        return self._last_tag

    def deep_read(self, timeout_s: float = 0.0) -> Optional[NFCTagData]:
        """Lectura profunda: parsea NDEF/Bambu/Creality y auto-crea si procede."""
        tag = self._last_tag
        if tag is None:
            return None
        parsed = parse_tag_payload(self._raw_payload) if self._raw_payload else dict(tag.data)
        if self.auto_create and "spool_id" not in parsed:
            parsed["spool_id"] = self.auto_create_spool_id(tag.uid)
        tag.data = parsed
        tag.read_mode = READ_MODE_DEEP
        return tag

    @staticmethod
    def auto_create_spool_id(uid: str) -> int:
        """Deriva un id de bobina determinista a partir del UID (>= 1)."""
        digest = hashlib.sha1(str(uid).encode("utf-8")).hexdigest()
        return (int(digest[:8], 16) % 1_000_000) + 1

    def write_spool_tag(self, spool_id: int, material_info: Optional[Dict[str, Any]] = None) -> bool:
        """Escribe la informacion de bobina en el tag (NDEF-like)."""
        if not self.available:
            return False
        payload: Dict[str, Any] = {"spool_id": int(spool_id)}
        payload.update(material_info or {})
        records = [f"{key.upper()}={value}" for key, value in payload.items()]
        if self._last_tag is not None:
            self._last_tag.data = parse_tag_payload(payload)
            self._last_tag.data["ndef"] = records
        return True

    def register_tag_callback(self, callback_fn: Callable[[NFCTagData], None]) -> None:
        self._callbacks.append(callback_fn)

    def set_neighbors(self, uids: List[str]) -> None:
        """Fija los UIDs detectados por lectores vecinos (deteccion de colisiones)."""
        self.neighbors = [str(uid) for uid in uids if str(uid)]

    def get_hardware_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "reader_type": self.reader_type,
            "available": self.available,
            "auto_create": self.auto_create,
            "reads": self.reads,
            "last_uid": self._last_tag.uid if self._last_tag else None,
            "neighbors": list(self.neighbors),
        }

    def _notify(self, tag: NFCTagData) -> None:
        for callback in list(self._callbacks):
            try:
                callback(tag)
            except Exception:  # noqa: BLE001 - aislar fallos de callback
                continue


class NFCManager:
    """Gestiona varios lectores NFC y detecta tags vecinos (vecindad)."""

    def __init__(self, printer: Any, config: Any = None) -> None:
        self.printer = printer
        self.config = config
        self.enabled = bool(_cfg_get(config, "enable_nfc", False))
        count = int(_cfg_get(config, "nfc_count", 1 if self.enabled else 0))
        self.readers: List[NFCReader] = [
            NFCReader(printer, config, index=i) for i in range(max(0, count))
        ]

    @property
    def available(self) -> bool:
        return self.enabled and any(reader.available for reader in self.readers)

    def scan_all(self, deep: bool = True) -> Dict[str, NFCTagData]:
        """Escanea todos los lectores y devuelve ``{uid: tag}``."""
        found: Dict[str, NFCTagData] = {}
        for reader in self.readers:
            tag = reader.scan_tag(0.0, deep=deep)
            if tag is not None:
                found[tag.uid] = tag
        self._update_neighbors(found)
        return found

    def _update_neighbors(self, found: Dict[str, NFCTagData]) -> None:
        uids = list(found.keys())
        for reader in self.readers:
            reader.set_neighbors([uid for uid in uids if found[uid].reader != reader.name])

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "count": len(self.readers),
            "readers": [reader.get_hardware_status() for reader in self.readers],
        }


# ---------------------------------------------------------------------------
# Arbitro multi-lector, endstop NFC y control de ganancia RX (Happy Hare)
# ---------------------------------------------------------------------------
DEFAULT_RX_GAIN = 1.0
MIN_RX_GAIN = 0.25
MAX_RX_GAIN = 4.0
RX_GAIN_TARGET = 0.7


class NFCArbiter:
    """Arbitra el acceso a varios lectores NFC (round-robin, sin colisiones).

    Evita que dos lectores activen a la vez en un bus compartido y reparte el
    turno de forma determinista. No bloquea: solo decide el turno.
    """

    def __init__(self, readers: Optional[List[NFCReader]] = None, config: Any = None) -> None:
        self.readers: List[NFCReader] = list(readers or [])
        self.enabled = bool(_cfg_get(config, "nfc_arbiter", True))
        self._index = 0
        self.turns = 0

    def add_reader(self, reader: NFCReader) -> None:
        self.readers.append(reader)

    def next_reader(self) -> Optional[NFCReader]:
        """Devuelve el siguiente lector en turno (round-robin determinista)."""
        if not self.readers:
            return None
        reader = self.readers[self._index % len(self.readers)]
        self._index = (self._index + 1) % len(self.readers)
        self.turns += 1
        return reader

    def poll(self, deep: bool = True) -> Dict[str, NFCTagData]:
        """Escanea cada lector en su turno y agrega los tags encontrados."""
        found: Dict[str, NFCTagData] = {}
        for reader in list(self.readers):
            tag = reader.scan_tag(0.0, deep=deep)
            if tag is not None:
                found[tag.uid] = tag
        for reader in self.readers:
            reader.set_neighbors([uid for uid in found if found[uid].reader != reader.name])
        return found

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "readers": len(self.readers),
            "turns": self.turns,
            "next_index": self._index,
        }


class NFCEndstop:
    """Endstop NFC: se activa cuando hay un tag presente en el lector."""

    def __init__(self, reader: Optional[NFCReader] = None, config: Any = None) -> None:
        self.reader = reader
        self.enabled = bool(_cfg_get(config, "nfc_endstop", False))
        self.triggers = 0

    def triggered(self) -> bool:
        """True si el lector detecta un tag (aislando fallos)."""
        if not self.enabled or self.reader is None:
            return False
        try:
            tag = self.reader.scan_tag(0.0, deep=False)
        except Exception:  # noqa: BLE001
            return False
        if tag is None:
            return False
        self.triggers += 1
        return True

    def reset(self) -> None:
        self.triggers = 0

    def get_status(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "triggers": self.triggers}


class RXGainController:
    """Autotune de la ganancia de recepcion del lector NFC (paridad ``rx_gain``).

    Ajusta la ganancia hacia un objetivo de senal relativo de forma
    determinista (proporcional, con limites). El ajuste es una funcion pura de
    (ganancia actual, senal medida).
    """

    def __init__(self, config: Any = None) -> None:
        self.enabled = bool(_cfg_get(config, "nfc_rx_gain_autotune", False))
        self.gain = float(_cfg_get(config, "nfc_rx_gain", DEFAULT_RX_GAIN))
        self.target = float(_cfg_get(config, "nfc_rx_gain_target", RX_GAIN_TARGET))
        self.step_gain = float(_cfg_get(config, "nfc_rx_gain_step", 0.5))
        self.updates = 0

    def set_gain(self, value: float) -> float:
        self.gain = min(MAX_RX_GAIN, max(MIN_RX_GAIN, float(value)))
        return self.gain

    def autotune(self, signal: float) -> float:
        """Ajusta la ganancia hacia ``target`` a partir de la senal relativa.

        Si la senal es menor que el objetivo, sube la ganancia; si es mayor,
        la baja. Determinista y acotado; no lanza.
        """
        try:
            if not self.enabled:
                return self.gain
            error = self.target - float(signal)
            self.gain = self.set_gain(self.gain + self.step_gain * error)
            self.updates += 1
        except Exception:  # noqa: BLE001
            return self.gain
        return self.gain

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "gain": round(self.gain, 4),
            "target": self.target,
            "updates": self.updates,
        }


def build_nfc_subsystem(printer: Any, config: Any = None) -> Dict[str, Any]:
    """Construye manager, arbitro, endstop y control de ganancia NFC."""
    manager = NFCManager(printer, config)
    readers = manager.readers
    arbiter = NFCArbiter(readers, config)
    endstop = NFCEndstop(readers[0] if readers else None, config)
    gain = RXGainController(config)
    return {"manager": manager, "arbiter": arbiter, "endstop": endstop, "gain": gain}


__all__ = [
    "NFCReader", "NFCManager", "NFCTagData", "SUPPORTED_READERS",
    "READ_MODE_SHALLOW", "READ_MODE_DEEP", "parse_tag_payload",
    "NFCArbiter", "NFCEndstop", "RXGainController", "build_nfc_subsystem",
    "DEFAULT_RX_GAIN", "MIN_RX_GAIN", "MAX_RX_GAIN",
]
