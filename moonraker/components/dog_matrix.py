"""Componente Moonraker para Dog Matrix MMU.

Expone endpoints HTTP/WebSocket y notificaciones para la UI (Mainsail, Fluidd,
KlipperScreen), y propaga cambios de estado desde Klipper (informe 6.13).

Instalacion: copiar como ``moonraker/components/dog_matrix.py`` y anadir
``[dog_matrix]`` a ``moonraker.conf``.

Requisitos: latencia de respuesta < 100 ms P99.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

# Moonraker inyecta dependencias via config.get_server(); no se importa
# moonraker directamente para permitir el test del modulo de forma aislada.

NOTIFICATION = "dog_matrix:state"

# --- Preprocesador de G-code ------------------------------------------------
_TOOL_RE = re.compile(r"^T(\d+)\b")
_TEMP_RE = re.compile(r"\bS(\d+)")
_COLOR_KEYS = {
    "filament_colour",
    "filament_color",
    "extruder_colour",
    "extruder_color",
}
_TEMP_CODES = ("M104", "M109")


def parse_gcode(text: str) -> Dict[str, Any]:
    """Extrae metadatos de un G-code para el MMU.

    Devuelve:

    - ``referenced_tools``: lista ordenada de herramientas (``T``) referenciadas.
    - ``total_toolchanges``: numero de cambios de herramienta efectivos.
    - ``colors``: colores por herramienta desde metadatos del slicer.
    - ``temperatures``: temperaturas de extrusor (``S`` de ``M104``/``M109``).
    """
    referenced: set = set()
    temperatures: set = set()
    colors: List[str] = []
    toolchanges = 0
    last_tool: Optional[int] = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            key, _, value = line[1:].partition("=")
            if key.strip().lower() in _COLOR_KEYS:
                colors = [item.strip() for item in re.split(r"[;,]", value) if item.strip()]
            continue
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        match = _TOOL_RE.match(code)
        if match:
            tool = int(match.group(1))
            referenced.add(tool)
            if tool != last_tool:
                toolchanges += 1
            last_tool = tool
            continue
        if code.upper().startswith(_TEMP_CODES):
            temp = _TEMP_RE.search(code)
            if temp:
                temperatures.add(int(temp.group(1)))

    return {
        "referenced_tools": sorted(referenced),
        "total_toolchanges": toolchanges,
        "colors": colors,
        "temperatures": sorted(temperatures),
    }


def _norm_color(value: Any) -> str:
    token = str(value).replace("#", "").strip()
    if len(token) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in token):
        return token.lower()
    return "".join(str(value).split()).upper()


def build_automap(
    tool_colors: Dict[int, Any],
    gate_colors: List[Any],
    gate_materials: Optional[List[Any]] = None,
) -> Dict[int, int]:
    """Automap determinista slicer-tool -> gate por color (y material).

    No reutiliza gates si hay suficientes; si un tool no encuentra color,
    recurre a identidad cuando el gate existe.
    """
    materials = [str(m).upper() for m in (gate_materials or [])]
    used: set = set()
    mapping: Dict[int, int] = {}
    for tool in sorted(tool_colors):
        color = _norm_color(tool_colors[tool])
        gate = None
        for index, gate_color in enumerate(gate_colors):
            if index in used:
                continue
            if color and _norm_color(gate_color) == color:
                gate = index
                break
        if gate is None and 0 <= tool < len(gate_colors) and tool not in used:
            gate = tool
        if gate is not None:
            used.add(gate)
            mapping[tool] = gate
    return mapping


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
        self.server.register_endpoint(
            "/server/dog_matrix/preprocess", ["POST"], self.handle_preprocess
        )
        self.server.register_endpoint(
            "/server/dog_matrix/gate_map", ["POST"], self.handle_gate_map
        )
        self.server.register_endpoint(
            "/server/dog_matrix/remap_ttg", ["POST"], self.handle_remap_ttg
        )
        self.server.register_endpoint(
            "/server/dog_matrix/maintenance", ["GET", "POST"], self.handle_maintenance
        )
        self.server.register_endpoint(
            "/server/dog_matrix/calibrate", ["POST"], self.handle_calibrate
        )
        self.server.register_endpoint(
            "/server/dog_matrix/automap", ["POST"], self.handle_automap
        )

    async def handle_gate_map(self, web_request: Any) -> Dict[str, Any]:
        """Edita un gate (estado/material/color/spool) desde la UI."""
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        gate = int(args.get("gate", 0))
        parts = [f"GATE={gate}"]
        for key in ("status", "material", "color", "spool_id", "availability"):
            if key in args and args[key] not in (None, ""):
                parts.append(f"{key.upper()}={args[key]}")
        result = await self._run_gcode("DM_GATE_MAP " + " ".join(parts))
        return {"ok": True, "result": result}

    async def handle_remap_ttg(self, web_request: Any) -> Dict[str, Any]:
        """Remapea tool->gate desde la UI."""
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        tool = int(args.get("tool", 0))
        gate = int(args.get("gate", 0))
        force = 1 if args.get("force") else 0
        result = await self._run_gcode(f"DM_REMAP_TTG TOOL={tool} GATE={gate} FORCE={force}")
        return {"ok": True, "result": result}

    async def handle_maintenance(self, web_request: Any) -> Dict[str, Any]:
        """Consulta o actualiza contadores de mantenimiento."""
        if hasattr(web_request, "get_args"):
            args = web_request.get_args() or {}
        else:
            args = {}
        if args.get("counter"):
            parts = [f"COUNTER={args['counter']}"]
            if "incr" in args:
                parts.append(f"INCR={int(args['incr'])}")
            if "limit" in args:
                parts.append(f"LIMIT={int(args['limit'])}")
            if args.get("reset"):
                parts.append("RESET=1")
            result = await self._run_gcode("DM_STATS " + " ".join(parts))
            return {"ok": True, "result": result}
        result = await self._run_gcode("DM_STATS SHOWCOUNTS=1")
        return {"ok": True, "result": result}

    async def handle_calibrate(self, web_request: Any) -> Dict[str, Any]:
        """Lanza una rutina de calibracion desde la UI."""
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        kind = str(args.get("kind", "gear")).upper()
        parts = []
        if "tool" in args:
            parts.append(f"TOOL={int(args['tool'])}")
        if "distance" in args:
            parts.append(f"DISTANCE_MM={args['distance']}")
        result = await self._run_gcode(f"DM_CALIBRATE_{kind} " + " ".join(parts))
        return {"ok": True, "result": result}

    async def handle_automap(self, web_request: Any) -> Dict[str, Any]:
        """Calcula un automap slicer->gate a partir de colores y material.

        Acepta ``content``/``gcode`` (G-code del slicer) y ``gate_colors`` /
        ``gate_materials`` (listas). Devuelve el comando ``DM_SLICER_TOOL_MAP``
        sugerido y el mapa calculado.
        """
        args = web_request.get_args() if hasattr(web_request, "get_args") else {}
        content = args.get("content") or args.get("gcode") or ""
        parsed = parse_gcode(content) if content else {}
        tool_colors = {
            int(tool): color
            for tool, color in enumerate(parsed.get("colors", []))
        }
        gate_colors = list(args.get("gate_colors", []) or [])
        gate_materials = list(args.get("gate_materials", []) or [])
        mapping = build_automap(tool_colors, gate_colors, gate_materials)
        command = "DM_SLICER_TOOL_MAP MAP=" + ",".join(
            str(mapping.get(tool, -1)) for tool in sorted(mapping)
        )
        return {"ok": True, "mapping": mapping, "command": command}

    async def handle_preprocess(self, web_request: Any) -> Dict[str, Any]:
        """Preprocesa un G-code y extrae metadatos de herramientas.

        Acepta el contenido directamente (``content``/``gcode``) o una ruta de
        fichero (``path``) validada contra ``gcode_root`` si esta configurado.
        """
        try:
            content = self._load_preprocess_content(web_request)
            if content is None:
                return {
                    "ok": False,
                    "error": "No se proporciono contenido G-code (content/gcode/path)",
                }
            parsed = parse_gcode(content)
            return {"ok": True, **parsed}
        except Exception as exc:  # noqa: BLE001 - endpoint defensivo
            logging.debug("dog_matrix: preprocess fallo: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _load_preprocess_content(self, web_request: Any) -> Optional[str]:
        """Obtiene el texto del G-code desde el argumento o una ruta segura."""
        if not hasattr(web_request, "get_argument"):
            return None
        content = web_request.get_argument("content", None) or web_request.get_argument("gcode", None)
        if content:
            return str(content)
        path = web_request.get_argument("path", None)
        if not path:
            return None
        real = os.path.realpath(str(path))
        try:
            root = self.config.get("gcode_root", None)
        except Exception:  # noqa: BLE001 - config no disponible
            root = None
        if root:
            real_root = os.path.realpath(str(root))
            if not (real == real_root or real.startswith(real_root + os.sep)):
                return None
        if not os.path.isfile(real):
            return None
        try:
            with open(real, "r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        except OSError:
            return None

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
