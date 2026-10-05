# Dog Matrix MMU — Manual de Seguridad

**Documento:** DOC-SAFETY · **Versión:** 0.1.0 · **Audiencia:** responsables de seguridad / integradores

> **Aviso:** Dog Matrix MMU es software de control de filamento. **No sustituye**
> a los sistemas de seguridad de la impresora. Los valores de tiempo y las
> afirmaciones de determinismo son **objetivos de diseño pendientes de
> validación HIL** y no constituyen una certificación. Cualquier uso en un
> entorno industrial requiere un análisis de seguridad específico del hardware
> final (p. ej. IEC 60204-1 / ISO 13849).

---

## 1. Alcance y principios

Principios de diseño aplicados en el código:

1. **Separación seguridad / conveniencia:** las operaciones físicas se validan
   antes de ejecutarse y nunca se reenvían de forma ambigua.
2. **Idempotencia:** cada toolchange tiene `operation_id`, `attempt` y
   `last_confirmed_step`.
3. **Estado seguro ante error:** ante fallo, la FSM pasa a `FAILED` y no emite
   nuevos movimientos.
4. **Reversibilidad:** toda escritura de configuración va precedida de snapshot
   con rollback automático ante fallo de verificación.
5. **Trazabilidad:** eventos estructurados y evidence bundles firmados.

---

## 2. Parada de emergencia

- La **parada de emergencia** es responsabilidad de Klipper (`M112`) y del
  hardware (botón físico). Dog Matrix **no** implementa un e-stop independiente.
- Dog Matrix **no debe** bloquear ni interceptar `M112`.
- Ante un `klippy:shutdown`, el módulo persiste el estado
  (`_handle_shutdown`) y deja la FSM inoperativa hasta reinicio.

---

## 3. Guardas de seguridad implementadas

| Guarda | Implementación | Requisito |
|---|---|---|
| Validación de rango de gate/tool | `StateMachine._validate` | DM-FSM-001 |
| Timeout por fase | `StateMachine.phase_timeouts` | DM-SAFE-003 |
| No reenvío de operación ambigua | `Recovery` (`manual_intervention` si duda) | DM-SAFE-001 |
| Revalidación de sensores antes de recuperar | `Recovery.revalidate_state` | DM-SAFE-003 |
| Verificación de filamento en toolhead | Fase `VERIFY` | DM-MMU-001 |
| Integridad de estado (checksum) | `Persistence` (SHA-256) | DM-PERSIST-001 |
| Validación de pines / esquema | `SystemValidator` | DM-CFG-001 |
| Rollback atómico | `RollbackManager` + `BackupManager` | DM-CFG-002 |

### 3.1 Política de recuperación

`Recovery.recover_from_failure` mapea el código de fase fallida a una acción:

| Código | Acción |
|---|---|
| `ERR_UNLOAD_FAILED` | `retry_unload` |
| `ERR_LOAD_FAILED` | `retry_load` |
| `ERR_SELECT_FAILED` | `home_selector` |
| `ERR_VERIFY_FAILED` | `revalidate_sensor` |
| Otro / duda | `manual_intervention` (no reenvía) |

Si `auto_recover=false`, la recuperación requiere confirmación del operador.

---

## 4. Limitaciones conocidas (leer antes de usar)

| Limitación | Impacto | Mitigación |
|---|---|---|
| **WCET no medido** | No se garantiza un tiempo máximo de detección/reacción | Medir en HIL; no usar como dato de seguridad |
| Sin e-stop independiente | Depende de Klipper/hardware | Instalar e-stop físico conforme a normativa |
| Flowguard en fase 2 | Detección de atasco no validada en producción | Validar con inyección de fallos |
| Sensores simulables | En ausencia de hardware, los sensores devuelven estado simulado | No confundir simulación con lectura real |
| Fallback Python | Sin CFFI, la latencia puede ser mayor | Compilar `_dm_native` para producción |

---

## 5. Modelo de seguridad del software

### 5.1 Gestión de secretos

- La API key de Moonraker y la clave de firma de evidencias se toman de
  **variables de entorno** (`DM_MOONRAKER_API_KEY`, `DM_EVIDENCE_KEY`), nunca de
  archivos versionados.
- No se registran secretos en los logs.

### 5.2 Validación de entrada

- Perfiles validados contra `profiles/schema.json` (JSON Schema) + reglas
  semánticas (`Capabilities.validate`).
- Rangos físicos verificados (velocidades, distancias).
- Escritura de configuración **atómica** y validada antes de aplicar.

### 5.3 Tolerancia a fallos de servicios

- Circuit breaker en Spoolman (3 fallos → abierto 30 s) con caché local.
- El estado se conserva ante reinicios (persistencia con checksum).

### 5.4 Auditoría

- Log estructurado JSON Lines por evento.
- Evidence bundle firmado (HMAC-SHA256) con hashes de los artefactos.

---

## 6. FMEA resumida

| Fallo | Efecto | Detección | Mitigación | Severidad |
|---|---|---|---|---|
| Runout | Impresión sin material | Sensor | Pausa / handoff EndlessSpool | Alta |
| Atasco (clog) | Flujo insuficiente | FlowGuard/encoder | Safe-stop | Alta |
| Selector bloqueado | Gate incorrecto | Sensor / timeout | Aislar, `manual_intervention` | Alta |
| MCU offline | Estado no verificable | Heartbeat | Bloqueo | Crítica |
| Spoolman offline | Sin sincronización | Timeout | Caché | Baja |
| Config corrupta | Klipper no arranca | Validación | Rollback | Alta |
| Sensor invertido | Decisión errónea | Calibración | Bloqueo | Alta |

---

## 7. Runbook de recuperación (operativo)

```text
1.  Detener operaciones (parada de Klipper si procede).
2.  Confirmar estado físico del filamento y del selector.
3.  Capturar evidencia:  dog-matrix doctor  +  DM_STATUS.
4.  Identificar el código de error (DM_RECOVER CODE=...).
5.  Consultar el último paso confirmado (campo last_confirmed_step).
6.  Ejecutar recuperación autorizada (DM_RECOVER) o intervenir manualmente.
7.  Revalidar sensores.
8.  Recalibrar si procede.
9.  Reanudar con confirmación.
10. Registrar el incidente (evidence bundle).
```

Comandos útiles:

```bash
bash install.sh doctor
bash install.sh validate --dest ~/printer_data/config
bash install.sh rollback --dest ~/printer_data/config
```

---

## 8. Requisitos previos a producción

Antes de usar Dog Matrix en un entorno real:

- [ ] Validación HIL del perfil de hardware concreto.
- [ ] Medición de WCET de detección y reacción.
- [ ] Inyección de fallos (runout, atasco, selector bloqueado, MCU offline).
- [ ] Prueba de recuperación y de rollback.
- [ ] Revisión del modelo de amenazas y de los permisos de Moonraker.
- [ ] Evidencia firmada conservada.

---

## 9. Referencias

- Instalación: [INSTALL.md](INSTALL.md)
- Diagnóstico: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Matriz FMEA ampliada: informe maestro, sección 19.3
