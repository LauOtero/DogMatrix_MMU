"""Capa de aceleracion nativa (CFFI/chelper) con fallback en Python puro.

Los kernels numericos de los caminos criticos (filtrado IIR, debounce de
sensores, deteccion de divergencia y planificacion de trayectoria) se delegan
a una biblioteca C compilada expuesta mediante CFFI (``_dm_native``). Si la
biblioteca nativa no esta disponible, se usa una implementacion de referencia
equivalente en Python que mantiene la misma semantica y firma.

Esta separacion permite determinismo temporal y bajo coste de CPU en hardware
real, sin impedir la ejecucion (y el test) en entornos sin compilador.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# --- Carga opcional de la biblioteca nativa --------------------------------
try:  # pragma: no cover - depende del entorno de compilacion
    from ._dm_native import ffi as _ffi  # type: ignore
    from ._dm_native import lib as _lib  # type: ignore

    NATIVE_AVAILABLE = True
except Exception:  # noqa: BLE001 - cualquier fallo => fallback Python
    _ffi = None
    _lib = None
    NATIVE_AVAILABLE = False


def native_available() -> bool:
    """Indica si la biblioteca nativa CFFI esta cargada."""
    return NATIVE_AVAILABLE


# --- Kernels ---------------------------------------------------------------
def iir_step(prev: float, new: float, alpha: float) -> float:
    """Filtro IIR de primer orden (alpha en [0,1], 1 => sin filtrado)."""
    if NATIVE_AVAILABLE:  # pragma: no cover
        return float(_lib.dm_iir_step(prev, new, alpha))
    alpha = min(1.0, max(0.0, alpha))
    return prev + alpha * (new - prev)


def median3(a: float, b: float, c: float) -> float:
    """Filtro de mediana sobre 3 muestras (elimina picos de ruido)."""
    if NATIVE_AVAILABLE:  # pragma: no cover
        return float(_lib.dm_median3(a, b, c))
    return max(min(a, b), min(max(a, b), c))


def divergence(requested_mm: float, measured_mm: float) -> Tuple[float, float]:
    """Devuelve (diferencia_mm, ratio) entre solicitado y medido.

    ``ratio`` es 0.0 cuando no se solicito movimiento (evita division por cero).
    """
    if NATIVE_AVAILABLE:  # pragma: no cover
        diff = float(_lib.dm_divergence_diff(requested_mm, measured_mm))
        ratio = float(_lib.dm_divergence_ratio(requested_mm, measured_mm))
        return diff, ratio
    diff = requested_mm - measured_mm
    ratio = abs(diff) / abs(requested_mm) if requested_mm else 0.0
    return diff, ratio


def debounce(
    stable_state: bool,
    candidate_state: bool,
    candidate_since: float,
    now: float,
    raw_state: bool,
    debounce_s: float,
) -> Tuple[bool, bool, float]:
    """Debounce por tiempo.

    Returns:
        (stable_state, candidate_state, candidate_since)
    """
    if NATIVE_AVAILABLE:  # pragma: no cover
        out = _ffi.new("int*"), _ffi.new("int*"), _ffi.new("double*")
        _lib.dm_debounce(
            int(stable_state),
            int(candidate_state),
            candidate_since,
            now,
            int(raw_state),
            debounce_s,
            out[0],
            out[1],
            out[2],
        )
        return bool(out[0][0]), bool(out[1][0]), float(out[2][0])
    if debounce_s <= 0.0:
        return raw_state, raw_state, now
    if raw_state != stable_state:
        if raw_state != candidate_state:
            candidate_state = raw_state
            candidate_since = now
        elif (now - candidate_since) >= debounce_s:
            stable_state = raw_state
    else:
        candidate_state = stable_state
        candidate_since = now
    return stable_state, candidate_state, candidate_since


def plan_trajectory(
    distance_mm: float,
    max_speed_mm_s: float,
    accel_mm_s2: float,
    jerk_mm_s3: float = 0.0,
    s_curve: bool = False,
    start_speed_mm_s: float = 0.0,
    end_speed_mm_s: float = 0.0,
) -> Dict[str, float]:
    """Planifica un perfil de velocidad (trapezoidal o S-curve).

    Devuelve un diccionario con las fases del perfil. No emite movimiento;
    es un calculo puro usado para validacion y para parametrizar el movimiento
    de Klipper (que realiza la interpolacion final en el MCU).
    """
    distance = abs(float(distance_mm))
    v_max = max(0.0, float(max_speed_mm_s))
    a_max = max(1e-6, float(accel_mm_s2))
    v0 = max(0.0, float(start_speed_mm_s))
    v1 = max(0.0, float(end_speed_mm_s))

    if distance <= 1e-9 or v_max <= 1e-9:
        return {
            "mode": "none",
            "peak_speed": 0.0,
            "t_accel": 0.0,
            "t_cruise": 0.0,
            "t_decel": 0.0,
            "total_time": 0.0,
        }

    # Distancia necesaria para acelerar a v_max y desacelerar a v1.
    d_accel_full = max(0.0, (v_max * v_max - v0 * v0)) / (2.0 * a_max)
    d_decel_full = max(0.0, (v_max * v_max - v1 * v1)) / (2.0 * a_max)

    if d_accel_full + d_decel_full <= distance:
        mode = "trapezoid"
        v_peak = v_max
        t_accel = (v_peak - v0) / a_max
        t_decel = (v_peak - v1) / a_max
        d_cruise = distance - d_accel_full - d_decel_full
        t_cruise = d_cruise / v_peak if v_peak > 0 else 0.0
    else:
        mode = "triangular"
        # Resolver v_peak con v0 y v1: (vp^2-v0^2)+(vp^2-v1^2) = 2*a*d
        v_peak = math.sqrt(max(0.0, a_max * distance + (v0 * v0 + v1 * v1) / 2.0))
        v_peak = min(v_peak, v_max)
        t_accel = max(0.0, (v_peak - v0) / a_max)
        t_decel = max(0.0, (v_peak - v1) / a_max)
        t_cruise = 0.0

    if s_curve and jerk_mm_s3 > 0:
        mode = "s_curve"
        # Tiempo de rampa de jerk a cada extremo del tramo de aceleracion.
        t_jerk = v_peak / max(1e-6, jerk_mm_s3)
        t_accel += 2.0 * t_jerk
        t_decel += 2.0 * t_jerk

    return {
        "mode": mode,
        "peak_speed": round(v_peak, 6),
        "t_accel": round(t_accel, 6),
        "t_cruise": round(t_cruise, 6),
        "t_decel": round(t_decel, 6),
        "total_time": round(t_accel + t_cruise + t_decel, 6),
    }


def sample_trajectory(plan: Dict[str, float], samples: int = 16) -> List[float]:
    """Muestrea la velocidad instantanea del perfil para diagnostico."""
    samples = max(1, int(samples))
    if plan.get("mode") == "none":
        return [0.0] * samples
    v_peak = plan["peak_speed"]
    t_a, t_c, t_d = plan["t_accel"], plan["t_cruise"], plan["t_decel"]
    total = max(1e-9, plan["total_time"])
    out: List[float] = []
    for i in range(samples):
        t = total * (i + 1) / samples
        if t <= t_a and t_a > 0:
            out.append(v_peak * (t / t_a))
        elif t <= t_a + t_c:
            out.append(v_peak)
        else:
            remaining = max(0.0, total - t)
            out.append(v_peak * (remaining / t_d) if t_d > 0 else 0.0)
    return out


__all__ = [
    "NATIVE_AVAILABLE",
    "native_available",
    "iir_step",
    "median3",
    "divergence",
    "debounce",
    "plan_trajectory",
    "sample_trajectory",
]
