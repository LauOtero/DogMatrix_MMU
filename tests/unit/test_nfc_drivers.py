"""Pruebas unitarias de la capa de drivers NFC (``dog_matrix.nfc_drivers``).

Se usa un transporte doble inyectado (sin hardware ni Klipper) que devuelve un
UID fijo, y variantes que fallan o solo exponen ``read``/``write`` para validar
la degradacion controlada y el fallback del transporte.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from dog_matrix.nfc_drivers import (
    NFCDriver,
    RC522Driver,
    SUPPORTED_DRIVERS,
    make_driver,
)

UID = b"\x04\xa1\xb2\xc3\xd4"
UID_HEX = UID.hex().upper()


class FakeTransport:
    """Transporte doble con ``transceive``; puede fallar a voluntad."""

    def __init__(self, response: Optional[bytes] = UID, fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls: List[bytes] = []

    def transceive(self, payload: bytes) -> Optional[bytes]:
        self.calls.append(bytes(payload))
        if self.fail:
            raise OSError("bus error")
        return self.response


class ReadWriteTransport:
    """Transporte sin ``transceive``: solo ``write`` + ``read`` (fallback)."""

    def __init__(self, response: Optional[bytes] = UID) -> None:
        self.response = response
        self.written: List[bytes] = []

    def write(self, payload: bytes) -> None:
        self.written.append(bytes(payload))

    def read(self) -> Optional[bytes]:
        return self.response


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_make_driver_valid_kind(kind):
    driver = make_driver(kind, transport=FakeTransport())
    assert isinstance(driver, NFCDriver)
    assert driver.driver_name == kind
    assert driver.get_status()["driver"] == kind


def test_make_driver_unknown_kind_returns_none():
    assert make_driver("desconocido", transport=FakeTransport()) is None
    assert make_driver(123, transport=FakeTransport()) is None


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_driver_available_without_transport(kind):
    driver = make_driver(kind)
    assert driver is not None
    assert driver.available is False
    assert driver.detect() is False
    assert driver.read_uid() is None


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_driver_detect_and_read_uid(kind):
    transport = FakeTransport()
    driver = make_driver(kind, transport=transport)
    assert driver is not None
    assert driver.available is True
    assert driver.detect() is True
    assert driver.read_uid() == UID_HEX
    # Se envio una trama propia (diferenciada por driver) al transporte.
    assert len(transport.calls) >= 2
    assert all(len(frame) > 0 for frame in transport.calls)


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_driver_transport_exception_degrades(kind):
    driver = make_driver(kind, transport=FakeTransport(fail=True))
    assert driver is not None
    assert driver.detect() is False
    assert driver.read_uid() is None


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_driver_read_and_write_block(kind):
    transport = FakeTransport(response=b"\x01\x02\x03\x04")
    driver = make_driver(kind, transport=transport)
    assert driver is not None
    assert driver.read_block(1) == b"\x01\x02\x03\x04"
    assert driver.write_block(2, b"\xaa\xbb") is True


@pytest.mark.parametrize("kind", SUPPORTED_DRIVERS)
def test_driver_block_operations_without_transport(kind):
    driver = make_driver(kind)
    assert driver is not None
    assert driver.read_block(0) is None
    assert driver.write_block(0, b"\x00") is False


def test_get_status_is_coherent():
    driver = make_driver("rc522", transport=FakeTransport())
    assert driver is not None
    status = driver.get_status()
    assert status["driver"] == "rc522"
    assert status["available"] is True
    assert status["reads"] == 0
    assert status["last_uid"] is None

    assert driver.read_uid() == UID_HEX
    status = driver.get_status()
    assert status["reads"] == 1
    assert status["last_uid"] == UID_HEX


def test_transport_read_write_fallback():
    transport = ReadWriteTransport(response=UID)
    driver = RC522Driver(transport=transport)
    assert driver.available is True
    assert driver.detect() is True
    assert driver.read_uid() == UID_HEX
    assert len(transport.written) >= 2


def test_driver_config_address_from_dict():
    driver = make_driver("pn532_i2c", transport=FakeTransport(), config={"nfc_address": 0x24})
    assert driver is not None
    assert driver.address == 0x24
