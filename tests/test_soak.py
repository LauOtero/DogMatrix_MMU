"""Pruebas de soak (estabilidad bajo carga prolongada) sin hardware.

Validan que el nucleo soporta ciclos repetidos de toolchange, comandos
intercalados y evaluaciones continuas de FlowGuard sin degradar el estado ni
perder contadores. Todo es determinista (RNG con semilla fija) y sin E/S de
log en el camino caliente.
"""

from __future__ import annotations

import random

import pytest

from dog_matrix.flowguard import (
    FLOW_CLOG,
    FLOW_DIVERGENCE,
    FLOW_OK,
    FLOW_RUNOUT,
    FLOW_TANGLE,
)

pytestmark = pytest.mark.slow

_VALID_FLOW_STATES = {FLOW_OK, FLOW_DIVERGENCE, FLOW_CLOG, FLOW_RUNOUT, FLOW_TANGLE}

#: Semilla fija para reproducibilidad estricta entre ejecuciones.
SOAK_SEED = 20261005


def test_soak_100_toolchanges(make_core):
    """100 toolchanges consecutivos mantienen contadores y estado consistentes."""
    core, printer = make_core(toolhead_present=True)
    gates = core.profile.gates

    for index in range(100):
        printer.gcode.run("DM_CHANGE", {"TOOL": index % gates})

    assert core.counters["toolchanges"] == 100
    assert core.counters["errors"] == 0
    assert core.get_status(0.0)["state"] == "COMPLETED"


def test_soak_commands_interleaved(make_core):
    """Toolchange + edicion de gate map + consulta de stats de forma intercalada."""
    core, printer = make_core(toolhead_present=True)
    gates = core.profile.gates

    for index in range(50):
        target = index % gates
        printer.gcode.run("DM_CHANGE", {"TOOL": target})
        printer.gcode.run(
            "DM_GATE_MAP",
            {"GATE": target, "MATERIAL": "PLA", "COLOR": "FFFFFF", "SPOOL_ID": f"SP-{index:03d}"},
        )
        printer.gcode.run("DM_STATS")

    assert core.counters["toolchanges"] == 50
    assert core.gate_filament[0]["material"] == "PLA"


def test_soak_flowguard_stability(make_core):
    """300 evaluaciones de FlowGuard producen estados validos y sin excepciones."""
    core, _ = make_core()
    rng = random.Random(SOAK_SEED)
    for _ in range(300):
        requested = rng.uniform(1.0, 200.0)
        measured = rng.uniform(0.0, 250.0)
        result = core.flowguard.evaluate(requested, measured)
        assert result.state in _VALID_FLOW_STATES

    stats = core.flowguard.get_statistics()
    assert stats["evaluations"] == 300


def test_soak_tangle_prevention_reduces_speed(make_core):
    """La deteccion confirmada de enredo activa la prevencion activa."""
    core, _ = make_core()
    # requested=100, measured=20 -> ratio 0.2 <= 0.3 -> tangle tras histeresis.
    for _ in range(3):
        result = core.flowguard.evaluate(100.0, 20.0)
    assert result.state == FLOW_TANGLE
    assert core.flowguard.speed_factor < 1.0
    assert core.flowguard.gear_current_factor > 1.0


def test_soak_stats_reset(make_core):
    """DM_STATS RESET=1 reinicia los contadores persistidos."""
    core, printer = make_core(toolhead_present=True)
    printer.gcode.run("DM_CHANGE", {"TOOL": 0})
    assert core.counters["toolchanges"] == 1

    printer.gcode.run("DM_STATS", {"RESET": 1})
    assert core.counters["toolchanges"] == 0
    assert core.persistence.load()["counters"]["toolchanges"] == 0

