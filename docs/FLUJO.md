# Flujo completo — de las gafas a los agentes, al RAG y de vuelta

Quién llama a quién, con qué protocolo y qué viaja en cada salto. Si algo no encaja
entre dos repos, la respuesta está aquí.

---

## El recorrido

```
┌─ Quest 3S / web ─────────────────────────────────────────────────────────┐
│  1. graba voz  ──audio──►  POST /voice/ask   (gate :8000, Whisper)       │
│                            ◄──texto──  el revisor LO LEE y pulsa Enviar  │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ 2. POST /bridge/explore {query, mode}
                               ▼
┌─ Gate de kevdev04 (Python) ──────────────────────────────────────────────┐
│  gate/bridge.py  ──3. WS /ws/explore──►  (Authorization: Bearer <token>) │
└──────────────────────────────┬───────────────────────────────────────────┘
                               ▼
┌─ Databricks App «lab-bridge» ────────────────────────────────────────────┐
│  https://lab-bridge-7474652340191726.aws.databricksapps.com              │
│                                                                          │
│  4. _events()  ─►  agent_lab/runtime.py::stream_discovery   ⏳ PENDIENTE │
│                         │  y para trabajar llama a:                      │
│                         ├─► POST /api/v1/evidence    pasajes curados     │
│                         ├─► POST /api/v1/ask         RAG rápido + score  │
│                         ├─► GET  /api/v1/documents/… el paper entero     │
│                         └─► POST /api/v1/hypothesis  puerta de procedencia│
│                         │                                                │
│                         ▼  índice vectorial + Unity Catalog              │
│              rag_v0_idx ─► documents_curated (21 907 trozos, aprobados)  │
│                                                                          │
│  5. emite eventos ──► stage · node · edge · citations · answer ·         │
│                       validation · approval_request · done               │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ 6. por el mismo WebSocket
                               ▼
┌─ Visor WebXR ────────────────────────────────────────────────────────────┐
│  el grafo crece en vivo; los nodos `agent_generated` van a revisión      │
│                                                                          │
│  7. la persona decide  ──► POST /api/v1/approve/{approval_id}            │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ 8. LA DECISIÓN VUELVE AL ORQUESTADOR
                               ▼
                   aprobacion.pedir() deja de bloquear y devuelve
                   'approve' | 'reject' | 'timeout'  →  cambia el plan
```

---

## La aprobación humana no termina en la interfaz

Es el punto que más fácil se implementa mal. El visor **no decide nada por su cuenta**:
sólo transporta la decisión. Quien la recibe es el orquestador, que estaba bloqueado
esperándola, y es él quien cambia el plan.

```python
from ar_vr_bridge import aprobacion

# dentro de stream_discovery, cuando el safety agent lo pide
yield ApprovalEvent(query_id=query_id, approval_id="ap_3c1d",
                    question="¿Publicamos esta afirmación en el visor?",
                    context=detalle)

decision = await aprobacion.pedir("ap_3c1d")      # bloquea hasta que responda alguien
if decision == "approve":
    ...                                            # sigue
else:
    ...                                            # reabre la suposición, prueba otro test
```

`aprobacion.pedir()` vive en `ar_vr_bridge/aprobacion.py` —fuera de `app.py`— para que
`runtime.py` lo importe sin ciclo. Al vencer el plazo devuelve `timeout`, y **el silencio
nunca cuenta como un sí**: quien llama decide qué hacer con eso.

Si nadie espera por ese `approval_id`, el endpoint responde `unknown_approval` en vez de
fingir que la decisión quedó registrada.

---

## Qué es real hoy y qué no

| Salto | Estado |
|---|---|
| 1. Voz → texto (Whisper, en la gate) | ✅ funciona; necesita `OPENAI_API_KEY` |
| 2. Gate → puente | ✅ funciona; **requiere `Authorization: Bearer`** |
| 3. WS `/ws/explore` | ✅ acepta y transmite |
| 4. **Orquestador** (`runtime.py`) | ⏳ **no existe** → se cae al simulador, y lo anuncia |
| 5. RAG y endpoints de datos | ✅ reales, medidos |
| 6. Eventos al visor | ✅ el visor ya los consume |
| 7-8. Aprobación → orquestador | ✅ la tubería está; falta quien la llame |

**El simulador ya no inventa la evidencia.** Desde ahora `/ws/explore` y `/api/v1/explore`
sacan sus citas del **índice vectorial real**, con su score, su pasaje y el filtro de
curación — y si la pregunta cae fuera del dominio, **devuelve cero citas** en vez de
fuentes de PET. Medido: *«MHETase role in PET degradation»* → 3 citas (0.854, 0.825,
0.806); *«first-line treatment for atrial fibrillation»* → **0**.

Lo que **sigue siendo guion** es el razonamiento: la hipótesis, el experimento y el
veredicto que emite el simulador son fijos. Eso es lo que aporta el orquestador. Hasta
entonces, **la evidencia de `/explore` es medible; su razonamiento no**.

---

## Latencias medidas

| Camino | Tiempo |
|---|---|
| `/api/v1/ask` en caliente | **~0,5 s** |
| `/api/v1/evidence` | ~2,6 s |
| `/api/v1/hypothesis` | ~1,3 s |
| `/api/v1/documents/{id}` | ~1 s (59 KB para 32 trozos) |
| Bucle `/explore` (simulador) | 12-15 s |

La voz tiene 1,5 s de presupuesto: por eso usa `/api/v1/ask` y no el bucle. Las dos vías
son independientes y se lanzan a la vez.

---

## Lo que cada repo debe tocar

| Pieza | Repo | Archivo |
|---|---|---|
| Captura de voz, Whisper, panel de revisión | `kevdev04/hacknation` | `gate/voice.py`, `vr/src/` |
| Relé al puente y decisiones | `kevdev04/hacknation` | `gate/bridge.py` |
| **Orquestador** | el equipo de agentes | `agent_lab/runtime.py` |
| Datos, RAG, procedencia, endpoints | este repo | `ar_vr_bridge/`, `agent_lab/procedencia*.py` |

Campos que devuelve cada endpoint: [`DATOS_POR_ENDPOINT.md`](DATOS_POR_ENDPOINT.md).
Contrato de transporte: [`API.md`](API.md). Esquema: `ar_vr_bridge/contract.py`.
