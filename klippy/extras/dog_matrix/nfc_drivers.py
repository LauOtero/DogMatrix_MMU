"""Capa de drivers NFC para el MMU Dog Matrix (paridad Happy Hare ``unit/nfc/*_driver.py``).

Cada lector (PN532 I2C/UART, PN5180, PN7160, RC522) implementa el mismo
contrato sobre un *transporte inyectado* (I2C/SPI/UART). El transporte es un
objeto con metodos ``transceive(payload)`` o ``read()``/``write()``; no se
depende de Klipper ni de hardware real.

Diseno determinista y resiliente:
- Sin bloqueo ni ``sleep``.
- Toda operacion aisla excepciones del transporte: ante fallo o ausencia de
  transporte se degrada devolviendo ``None``/``False`` sin propagar.

Las tramas son simbolicas y cortas (suficientes para dobles de test), pero
estan diferenciadas por driver y documentadas mediante comentario.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

SUPPORTED_DRIVERS = ("pn532_i2c", "pn532_uart", "pn5180", "pn7160", "rc522")


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


class NFCDriverError(Exception):
    """Error de driver NFC (reservado para fallos explicitos de alto nivel)."""


# --- Tramas PN532 (I2C/UART) -------------------------------------------------
PN532_WAKEUP = b"\x55\x55"  # Preambulo de despertar requerido en UART.
PN532_HOST_TO_PN532 = 0xD4  # TFI host->PN532.
PN532_CMD_GET_FIRMWARE = 0x02
PN532_CMD_IN_LIST_PASSIVE = 0x4A  # InListPassiveTarget (106 kbps, 1 target).


def _pn532_frame(cmd: int, params: bytes = b"") -> bytes:
    """Construye una trama PN532 (preamble+LEN+LCS+TFI/CMD+DCS+postamble)."""
    body = bytes([PN532_HOST_TO_PN532, cmd]) + params
    length = len(body) & 0xFF
    lcs = (0x100 - length) & 0xFF
    dcs = (0x100 - (sum(body) & 0xFF)) & 0xFF
    return b"\x00\x00\xff" + bytes([length, lcs]) + body + bytes([dcs, 0x00])


# --- Tramas PN5180 (SPI) -----------------------------------------------------
PN5180_CMD_GET_UID = 0x0A  # Comando simbolico de inventario ISO14443A.


# --- Tramas PN7160 (NCI) -----------------------------------------------------
# Cabecera NCI: byte MT/GID/OID = 0x21 (RF Management, RF_DISCOVER_CMD),
# longitud 0x03 y payload de 3 bytes de configuracion de descubrimiento.
PN7160_NCI_RF_DISCOVER = bytes([0x21, 0x00, 0x03, 0x01, 0x01, 0x00])


# --- Tramas RC522 (SPI) ------------------------------------------------------
RC522_CMD_REQA = 0x26  # REQA (0x26) para solicitar tags ISO14443A en reposo.
RC522_REG_VERSION = 0x37  # VersionReg para ping del lector.


class NFCDriver:
    """Driver NFC base sobre un transporte inyectado.

    Subclases sobrescriben ``_read_uid_raw`` y, opcionalmente, ``_ping_frame``
    para enviar su trama propia a traves de ``_transceive``.
    """

    driver_name: str = "nfc"
    # Trama de ping/version por defecto (sobreescrita por cada subclase).
    PING_FRAME: bytes = b"\x00"
    # Comandos genericos de bloque (ISO14443A). No requieren ser reales.
    READ_BLOCK_CMD: int = 0x30
    WRITE_BLOCK_CMD: int = 0xA0
    # Trama UID por defecto (sobreescrita por cada subclase).
    UID_FRAME: bytes = b"\x00"

    def __init__(self, transport: Any = None, config: Any = None) -> None:
        self.transport = transport
        self.config = config
        self.address = _cfg_get(config, "nfc_address", None)
        self.reads = 0
        self.last_uid: Optional[str] = None

    # -- Capacidad ----------------------------------------------------------
    @property
    def available(self) -> bool:
        """True si hay transporte con el metodo requerido (transceive o read+write)."""
        transport = self.transport
        if transport is None:
            return False
        if callable(getattr(transport, "transceive", None)):
            return True
        return callable(getattr(transport, "read", None)) and callable(
            getattr(transport, "write", None)
        )

    # -- API publica --------------------------------------------------------
    def detect(self) -> bool:
        """Devuelve True si el lector responde al ping (con defensa de errores)."""
        if not self.available:
            return False
        try:
            return bool(self._ping())
        except Exception:  # noqa: BLE001 - degradacion controlada
            return False

    def read_uid(self) -> Optional[str]:
        """Lee el UID y lo devuelve en hexadecimal en mayusculas, o None."""
        if not self.available:
            return None
        try:
            raw = self._read_uid_raw()
        except Exception:  # noqa: BLE001 - degradacion controlada
            return None
        if not raw:
            return None
        uid = bytes(raw).hex().upper()
        self.reads += 1
        self.last_uid = uid
        return uid

    def read_block(self, block: int = 0) -> Optional[bytes]:
        """Lee un bloque de datos, o None si no es posible."""
        if not self.available:
            return None
        try:
            response = self._transceive(self._block_read_frame(block))
        except Exception:  # noqa: BLE001 - degradacion controlada
            return None
        return bytes(response) if response is not None else None

    def write_block(self, block: int, data: bytes) -> bool:
        """Escribe un bloque de datos; True si el transporte acepto la operacion."""
        if not self.available:
            return False
        try:
            response = self._transceive(self._block_write_frame(block, data))
        except Exception:  # noqa: BLE001 - degradacion controlada
            return False
        return response is not None

    # -- Hooks a sobrescribir ----------------------------------------------
    def _ping(self) -> bool:
        """Ping del lector usando la trama por defecto."""
        return self._transceive(self._ping_frame()) is not None

    def _ping_frame(self) -> bytes:
        return bytes(self.PING_FRAME)

    def _read_uid_raw(self) -> Optional[bytes]:
        """Debe devolver el UID crudo usando ``_transceive``."""
        return self._transceive(bytes(self.UID_FRAME))

    # -- Transporte ---------------------------------------------------------
    def _transceive(self, payload: bytes) -> Optional[bytes]:
        """Envia ``payload`` por el transporte aislando cualquier excepcion.

        Prefiere ``transport.transceive``; si no existe, usa ``write`` + ``read``.
        """
        transport = self.transport
        if transport is None:
            return None
        try:
            transceive = getattr(transport, "transceive", None)
            if callable(transceive):
                result = transceive(payload)
            else:
                writer = getattr(transport, "write", None)
                reader = getattr(transport, "read", None)
                if not (callable(writer) and callable(reader)):
                    return None
                writer(payload)
                result = reader()
        except Exception:  # noqa: BLE001 - aislar fallos de bus/hardware
            return None
        if result is None:
            return None
        return bytes(result)

    # -- Tramas de bloque ---------------------------------------------------
    def _block_read_frame(self, block: int) -> bytes:
        return bytes([self.READ_BLOCK_CMD, int(block) & 0xFF])

    def _block_write_frame(self, block: int, data: bytes) -> bytes:
        return bytes([self.WRITE_BLOCK_CMD, int(block) & 0xFF]) + bytes(data or b"")

    # -- Estado -------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "driver": self.driver_name,
            "available": self.available,
            "reads": self.reads,
            "last_uid": self.last_uid,
        }


class PN532I2CDriver(NFCDriver):
    """PN532 sobre I2C (HSU/emulacion via direccion I2C)."""

    driver_name = "pn532_i2c"
    PING_FRAME = _pn532_frame(PN532_CMD_GET_FIRMWARE)

    def _read_uid_raw(self) -> Optional[bytes]:
        # InListPassiveTarget por I2C: 1 target a 106 kbps, sin wakeup previo.
        frame = _pn532_frame(PN532_CMD_IN_LIST_PASSIVE, b"\x01\x00")
        return self._transceive(frame)


class PN532UARTDriver(NFCDriver):
    """PN532 sobre UART (HSU) con preambulo de despertar."""

    driver_name = "pn532_uart"
    PING_FRAME = PN532_WAKEUP + _pn532_frame(PN532_CMD_GET_FIRMWARE)

    def _read_uid_raw(self) -> Optional[bytes]:
        # Igual que I2C pero precedido por el wakeup 0x55 0x55 de UART.
        frame = PN532_WAKEUP + _pn532_frame(PN532_CMD_IN_LIST_PASSIVE, b"\x01\x00")
        return self._transceive(frame)


class PN5180Driver(NFCDriver):
    """PN5180 sobre SPI (inventario ISO14443A)."""

    driver_name = "pn5180"
    PING_FRAME = bytes([PN5180_CMD_GET_UID, 0x00, 0x00])

    def _read_uid_raw(self) -> Optional[bytes]:
        # El PN5180 requiere cargar el comando y luego leer la respuesta;
        # trama simbolica de inventario.
        frame = bytes([PN5180_CMD_GET_UID, 0x00, 0x00])
        return self._transceive(frame)


class PN7160Driver(NFCDriver):
    """PN7160 sobre I2C en modo NCI (RF discover)."""

    driver_name = "pn7160"
    PING_FRAME = bytes([0x20, 0x00, 0x00])  # CORE_RESET/STATUS simbolico.

    def _read_uid_raw(self) -> Optional[bytes]:
        # Paquete NCI RF_DISCOVER_CMD (descubrimiento de tags ISO14443A).
        return self._transceive(PN7160_NCI_RF_DISCOVER)


class RC522Driver(NFCDriver):
    """RC522 sobre SPI (REQA / anticolision)."""

    driver_name = "rc522"
    PING_FRAME = bytes([0x80 | RC522_REG_VERSION, 0x00])  # Lectura de VersionReg.

    def _read_uid_raw(self) -> Optional[bytes]:
        # REQA (0x26): solicita tags en reposo; el UID llega en la respuesta.
        return self._transceive(bytes([RC522_CMD_REQA]))


_DRIVER_REGISTRY: Dict[str, Type[NFCDriver]] = {
    "pn532_i2c": PN532I2CDriver,
    "pn532_uart": PN532UARTDriver,
    "pn5180": PN5180Driver,
    "pn7160": PN7160Driver,
    "rc522": RC522Driver,
}


def make_driver(kind: str, transport: Any = None, config: Any = None) -> Optional[NFCDriver]:
    """Factoría de drivers por nombre; devuelve None si ``kind`` es desconocido."""
    if not isinstance(kind, str):
        return None
    driver_cls = _DRIVER_REGISTRY.get(kind.strip().lower())
    if driver_cls is None:
        return None
    return driver_cls(transport=transport, config=config)


__all__ = [
    "NFCDriverError",
    "NFCDriver",
    "PN532I2CDriver",
    "PN532UARTDriver",
    "PN5180Driver",
    "PN7160Driver",
    "RC522Driver",
    "SUPPORTED_DRIVERS",
    "make_driver",
]
