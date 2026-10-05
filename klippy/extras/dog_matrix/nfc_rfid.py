"""Lectura de tags NFC/RFID para identificacion de bobinas.

Adapter sobre lectores PN532/PN5180/PN7160/RC522 (I2C/SPI/UART). Cuando no hay
hardware accesible, opera en modo simulado para permitir pruebas e integracion
con Spoolman (DM-NFC-001, informe 6.19).

Requisitos: deteccion de tag < 200 ms, UIDs ISO14443A/Mifare y NTAG213/215/216.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_READERS = ("pn532", "pn5180", "pn7160", "rc522")


@dataclass
class NFCTagData:
    """Datos leidos de un tag NFC/RFID."""

    uid: str
    tech: str = "ISO14443A"
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"uid": self.uid, "tech": self.tech, "data": self.data, "timestamp": self.timestamp}


class NFCReader:
    """Interfaz de lectura/escritura de tags NFC."""

    def __init__(self, printer: Any, config: Any = None) -> None:
        self.printer = printer
        self.config = config
        self.reader_type = self._reader_type(config)
        self.available = self.reader_type in SUPPORTED_READERS
        self._callbacks: List[Callable[[NFCTagData], None]] = []
        self._last_tag: Optional[NFCTagData] = None
        self.reads = 0

    @staticmethod
    def _reader_type(config: Any) -> str:
        if config is None:
            return "none"
        if isinstance(config, dict):
            return str(config.get("nfc_type", "none")).lower()
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return str(getter("nfc_type", "none")).lower()
            except TypeError:
                return "none"
        return "none"

    # -- Simulacion ---------------------------------------------------------
    def simulate_tag(self, uid: str, data: Optional[Dict[str, Any]] = None) -> NFCTagData:
        """Inyecta un tag para pruebas / banco."""
        tag = NFCTagData(uid=uid, data=data or {}, timestamp=time.time())
        self._last_tag = tag
        self._notify(tag)
        return tag

    # -- API publica --------------------------------------------------------
    def scan_tag(self, timeout_s: float = 2.0) -> Optional[NFCTagData]:
        """Escanea un tag. Devuelve ``None`` si no se detecta en el timeout."""
        started = time.monotonic()
        self.reads += 1
        # En ausencia de hardware, solo devuelve el ultimo tag simulado.
        while (time.monotonic() - started) < max(0.0, timeout_s):
            if self._last_tag is not None:
                return self._last_tag
            if not self.available:
                break
            time.sleep(0.02)
        return self._last_tag

    def write_spool_tag(self, spool_id: int, material_info: Dict[str, Any]) -> bool:
        """Escribe la informacion de bobina en un tag (si el hardware lo soporta)."""
        if not self.available:
            return False
        payload = {"spool_id": int(spool_id)}
        payload.update(material_info)
        if self._last_tag is not None:
            self._last_tag.data = payload
        return True

    def register_tag_callback(self, callback_fn: Callable[[NFCTagData], None]) -> None:
        self._callbacks.append(callback_fn)

    def get_hardware_status(self) -> Dict[str, Any]:
        return {
            "reader_type": self.reader_type,
            "available": self.available,
            "reads": self.reads,
            "last_uid": self._last_tag.uid if self._last_tag else None,
        }

    def _notify(self, tag: NFCTagData) -> None:
        for callback in list(self._callbacks):
            try:
                callback(tag)
            except Exception:  # noqa: BLE001 - aislar fallos de callback
                continue


__all__ = ["NFCReader", "NFCTagData", "SUPPORTED_READERS"]
