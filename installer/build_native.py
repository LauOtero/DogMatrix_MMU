"""Compila la biblioteca nativa CFFI de Dog Matrix MMU.

Genera el modulo ``dog_matrix._dm_native`` a partir de ``csrc/dm_native.c``.
Si ``cffi`` no esta instalado, el runtime usa el fallback Python de
``_native.py`` (mismas firmas y semantica).

Uso:
    python -m installer.build_native
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KLIPPY_EXTRAS = PROJECT_ROOT / "klippy" / "extras"
C_SOURCE = KLIPPY_EXTRAS / "dog_matrix" / "csrc" / "dm_native.c"

CDEF = """
double dm_iir_step(double prev, double new_value, double alpha);
double dm_median3(double a, double b, double c);
double dm_divergence_diff(double requested_mm, double measured_mm);
double dm_divergence_ratio(double requested_mm, double measured_mm);
void dm_debounce(int stable_state, int candidate_state, double candidate_since,
                 double now, int raw_state, double debounce_s,
                 int *out_stable, int *out_candidate, double *out_candidate_since);
"""


def main() -> int:
    try:
        from cffi import FFI  # type: ignore
    except ImportError:
        print("cffi no instalado: se usara el fallback Python en runtime.", file=sys.stderr)
        return 0
    if not C_SOURCE.exists():
        print(f"fuente C no encontrada: {C_SOURCE}", file=sys.stderr)
        return 1

    if str(KLIPPY_EXTRAS) not in sys.path:
        sys.path.insert(0, str(KLIPPY_EXTRAS))

    ffibuilder = FFI()
    ffibuilder.cdef(CDEF)
    ffibuilder.set_source(
        "dog_matrix._dm_native",
        f'#include "{C_SOURCE.as_posix()}"',
        extra_compile_args=["-O2", "-ffast-math"],
    )
    ffibuilder.compile(tmpdir=str(PROJECT_ROOT / "build"), verbose=True)
    print("Biblioteca nativa compilada: dog_matrix._dm_native")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
