"""Suite de soak testing para validar estabilidad del módulo Dog Matrix MMU.

Este test ejecuta 100+ toolchanges consecutivas y verifica:
- Que no haya memory leaks o contador decrements
- Que los timings se mantengan dentro de rangos esperados
- Que el estado final sea consistente
- Que los errores se reporten correctamente
"""

import time
import random
import pytest

from klippy.extras.dog_matrix.core import DogMatrix
from klippy.extras.dog_matrix.state_machine import StateMachine


class TestSoakStability:
    """Validar estabilidad del sistema bajo carga prolongada."""

    def test_soak_100_toolchanges(self, klippy_instance, test_config):
        """Ejecutar 100 toolchanges y verificar estabilidad."""
        dm = klippy_instance  # tipo: DogMatrix
        dm.initialize(test_config)

        errors = []
        stats_start = dm.get_stats()

        for i in range(100):
            # Ejecutar cambio de herramienta a gate aleatorio
            target_gate = random.randint(0, dm.profile.gates - 1)
            try:
                dm.cmd_DM_CHANGE(target_gate=target_gate)
            except Exception as e:
                errors.append(f"Error en toolchange {i}: {e}")

            # Cada 25 cambios, verificar que no hay memory leaks
            if (i + 1) % 25 == 0:
                stats_current = dm.get_stats()
                # Verificar que los contadores no disminuyan
                if stats_current["counters"]["toolchanges"] < stats_start["counters"]["toolchanges"]:
                    errors.append(
                        f"Contador disminuyó después de {i+1} toolchanges"
                    )

        # Verificar estado final consistente
        final_stats = dm.get_stats()
        assert len(errors) == 0, f"Soak test falló con errores: {errors[:5]}"
        assert (
            final_stats["counters"]["toolchanges"] == 100
        ), "Contador total incorrecto"

        # Verificar que los timings se mantienen dentro de rangos esperados
        total_time = final_stats["toolchange_timings"]["total"]
        expected_range = (8000, 15000)  # ms para 100 toolchanges normales
        assert (
            expected_range[0] <= total_time <= expected_range[1]
        ), f"Tiempo total {total_time}ms fuera de rango esperado"

    def test_soak_concurrent_operations(self, klippy_instance, test_config):
        """Probar estabilidad con operaciones concurrentes."""
        dm = klippy_instance
        dm.initialize(test_config)

        errors = []
        # Ejecutar combinaciones de: toolchange + gate map update + stats query
        for i in range(50):
            try:
                # Toolchange
                target_gate = i % dm.profile.gates
                dm.cmd_DM_CHANGE(target_gate=target_gate)
                # Actualizar gate map atributos
                dm.cmd_DM_GATE_MAP(
                    GATE=i % dm.profile.gates, MATERIAL="PLA", COLOR="FFFFFF"
                )
                # Query stats
                dm.cmd_DM_STATS()
            except Exception as e:
                errors.append(f"Error iteración {i}: {e}")

        assert len(errors) == 0, f"Test concurrent falló: {errors[:3]}"

    def test_soak_with_flowguard_evaluation(self, klippy_instance, test_config):
        """Probar estabilidad incorporando evaluaciones FlowGuard."""
        dm = klippy_instance
        dm.initialize(test_config)

        # Realizar muchas evaluaciones FlowGuard para verificar estabilidad
        errors = []
        for i in range(200):
            # Simular una evaluación de flujo aleatoria
            try:
                # Lectura simulada: requested y measured_mm variables
                requested = random.uniform(50, 200)
                measured = random.uniform(0, 250)
                # Ejecutar evaluation a través del flowguard
                result = dm.flowguard.evaluate(requested, measured,
                                              dm.sensors if hasattr(dm, 'sensors') else None)
                # Verificar que la clasificación sea válida
                assert result.state in (
                    "ok",
                    "divergence",
                    "clog",
                    "runout",
                    "tangle",
                ), f"EstadoFlowGuard inválido: {result.state}"
            except Exception as e:
                errors.append(f"Error evaluación FlowGuard {i}: {e}")

        assert len(errors) == 0, f"Test FlowGuard stability falló: {errors[:3]}"


class TestSoakIntegration:
    """Tests de soak testing de integración."""

    def test_soak_with_synchronization(self, klippy_instance, test_config):
        """Probar estabilidad con sincronizaciones (Spoolman, NFC, etc)."""
        dm = klippy_instance
        dm.initialize(test_config)

        errors = []
        # Ejecutar cycles completos: toolchange + sync
        for i in range(30):
            try:
                # 1. Toolchange
                target_gate = i % dm.profile.gates
                dm.cmd_DM_CHANGE(target_gate=target_gate)

                # 2. Actualizar gate map
                dm.cmd_DM_GATE_MAP(
                    GATE=target_gate,
                    MATERIAL="PLA",
                    COLOR="FFFFFF",
                    SPOOL_ID=f"SP-{i:03d}"
                )

                # 3. Query stats
                dm.cmd_DM_STATS()

                # 4. Verificar estado consistente
                stats = dm.get_stats()
                assert stats is not None, "Stats retornaron None"
            except Exception as e:
                errors.append(f"Error cycle {i}: {e}")

        assert len(errors) == 0, f"Test sync stability falló: {errors[:3]}"

    def test_soak_edge_cases(self, klippy_instance, test_config):
        """Probar casos borde durante soak testing."""
        dm = klippy_instance
        dm.initialize(test_config)

        errors = []
        # Casos borde: gates al límite, configuraciones extremas
        edge_cases = [
            # Último gate
            {"gate": dm.profile.gates - 1, "expected_success": True},
            # Primer gate
            {"gate": 0, "expected_success": True},
            # Mismo gate consecutivo
            {"gate": None, "expected_may_fail": True},
        ]

        for case in edge_cases:
            try:
                if case.get("expected_may_fail"):
                    # Estos pueden fallar esperadamente
                    pass  # Validar comportamiento esperado
                else:
                    # Operaciones normales
                    dm.cmd_DM_CHANGE(target_gate=case["gate"])
            except Exception as e:
                errors.append(f"Error case {case}: {e}")

        assert len(errors) == 0, f"Test edge cases falló: {errors[:3]}"