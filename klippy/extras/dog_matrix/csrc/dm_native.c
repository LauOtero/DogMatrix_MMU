/* Dog Matrix MMU - kernels nativos (C) expuestos via CFFI.
 *
 * Implementa los caminos criticos de computo numerico que en produccion se
 * ejecutan fuera del interprete Python para garantizar determinismo temporal:
 *   - filtrado IIR de primer orden (encoder / sensores)
 *   - filtro de mediana de 3 muestras
 *   - calculo de divergencia (FlowGuard)
 *   - maquina de debounce temporal (sensores)
 *
 * La biblioteca se compila con cffi (ver installer/build_native.py) como
 * modulo `_dm_native`. Si no se compila, `_native.py` usa el fallback Python.
 */

#include <math.h>

double dm_iir_step(double prev, double new_value, double alpha) {
    if (alpha < 0.0) {
        alpha = 0.0;
    } else if (alpha > 1.0) {
        alpha = 1.0;
    }
    return prev + alpha * (new_value - prev);
}

double dm_median3(double a, double b, double c) {
    double lo = a < b ? a : b;
    double hi = a > b ? a : b;
    if (c < lo) {
        return lo;
    }
    if (c > hi) {
        return hi;
    }
    return c;
}

double dm_divergence_diff(double requested_mm, double measured_mm) {
    return requested_mm - measured_mm;
}

double dm_divergence_ratio(double requested_mm, double measured_mm) {
    if (requested_mm == 0.0) {
        return 0.0;
    }
    return fabs(requested_mm - measured_mm) / fabs(requested_mm);
}

/* Maquina de debounce temporal.
 * out_stable / out_candidate: 1 = activo, 0 = inactivo.
 */
void dm_debounce(int stable_state, int candidate_state, double candidate_since,
                 double now, int raw_state, double debounce_s,
                 int *out_stable, int *out_candidate, double *out_candidate_since) {
    if (raw_state != stable_state) {
        if (raw_state != candidate_state) {
            candidate_state = raw_state;
            candidate_since = now;
        } else if ((now - candidate_since) >= debounce_s) {
            stable_state = raw_state;
        }
    } else {
        candidate_state = stable_state;
        candidate_since = now;
    }
    *out_stable = stable_state;
    *out_candidate = candidate_state;
    *out_candidate_since = candidate_since;
}
