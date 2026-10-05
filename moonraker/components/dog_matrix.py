"""Componente Moonraker para Dog Matrix MMU.

Expone endpoints HTTP/WebSocket y notificaciones para la UI (Mainsail, Fluidd,
KlipperScreen), y propaga cambios de estado desde Klipper (informe 6.13).

Instalacion: copiar como ``moonraker/components/dog_matrix.py`` y anadir
``[dog_matrix]`` a ``moonraker.conf``.

Requisitos: latencia de respuesta < 100 ms P99.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

# Moonraker inyecta dependencias via config.get_server(); no se importa
# moonraker directamente para permitir el test del modulo de forma aislada.

NOTIFICATION = "dog_matrix:state"


class DogMatrixComponent:
    """Componente Moonraker de integracion del MMU."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.last_status: Dict[str, Any] = {}
        self.klippy_connected = False
        self._remote_methods = False

        self.register_endpoints()
        self.register_notifications()

        self.server.register_event_handler("server:klippy_ready", self._on_klippy_ready)
        self.server.register_event_handler("server:klippy_disconnect", self._on_klippy_disconnect)

    # -- Registro -----------------------------------------------------------
    def register_endpoints(self) -> None:
        self.server.register_endpoint(
            "/server/dog_matrix/status", ["GET"], self.handle_status
        )
        self.server.register_endpoint(
            "/server/dog_matrix/toolchange", ["POST"], self.handle_toolchange
        )
        self.server.register_endpoint(
            "/server/dog_matrix/recover", ["POST"], self.handle_recover
        )

    def register_notifications(self) -> None:
        self.server.register_notification(NOTIFICATION)
        # Metodo remoto invocado desde Klipper para empujar el estado.
        try:
            self.server.register_remote_method("dog_matrix_status", self._on_status_update)
            self._remote_methods = True
        except Exception:  # noqa: BLE001 - versiones antiguas de Moonraker
            self._remote_methods = False

    # -- Ciclo de vida ------------------------------------------------------
    async def _on_klippy_ready(self) -> None:
        self.klippy_connected = True
        await self._refresh_status()

    async def _on_klippy_disconnect(self) -> None:
        self.klippy_connected = False

    # -- Endpoints ----------------------------------------------------------
    async def handle_status(self, web_request: Any) -> Dict[str, Any]:
        return {"status": await self._refresh_status()}

    async def handle_toolchange(self, web_request: Any) -> Dict[str, Any]:
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        tool = int(args.get("tool", 0))
        result = await self._run_gcode(f"DM_CHANGE TOOL={tool}")
        return {"ok": True, "tool": tool, "result": result}

    async def handle_recover(self, web_request: Any) -> Dict[str, Any]:
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        code = str(args.get("code", ""))
        result = await self._run_gcode(f"DM_RECOVER CODE={code}")
        return {"ok": True, "result": result}

    # -- Internos -----------------------------------------------------------
    async def _refresh_status(self) -> Dict[str, Any]:
        if not self.klippy_connected:
            return dict(self.last_status)
        try:
            status = await self.klippy_apis.query_objects({"dog_matrix": None})
            self.last_status = status.get("dog_matrix", {}) or {}
        except Exception as exc:  # noqa: BLE001 - klippy no disponible
            logging.debug("dog_matrix: query_objects fallo: %s", exc)
        return dict(self.last_status)

    async def _run_gcode(self, script: str) -> str:
        try:
            return await self.klippy_apis.run_gcode(script)
        except Exception as exc:  # noqa: BLE001
            raise self.server.error(f"gcode fallo: {exc}") from exc

    def _on_status_update(self, status: Dict[str, Any]) -> None:
        """Recibe el estado empujado desde Klipper y lo notifica a la UI."""
        self.last_status = dict(status or {})
        for method in ("send_notification", "notify"):
            sender = getattr(self.server, method, None)
            if callable(sender):
                try:
                    sender(NOTIFICATION, self.last_status)
                except Exception:  # noqa: BLE001
                    pass
                break

    def get_status(self, eventtime: Optional[float] = None) -> Dict[str, Any]:
        return {"last_status": dict(self.last_status), "connected": self.klippy_connected}


def load_component(config: Any) -> DogMatrixComponent:
    """Punto de entrada del componente Moonraker."""
    return DogMatrixComponent(config)


__all__ = ["DogMatrixComponent", "load_component", "NOTIFICATION"]
