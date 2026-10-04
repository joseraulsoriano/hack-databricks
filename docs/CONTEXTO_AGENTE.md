# Contexto para el orquestador — qué hay, qué se puede pedir y qué no existe

Documento de alimentación del agente orquestador. Todo lo de aquí está **medido contra el
workspace real el 2026-10-03**, no estimado. Si un número no lleva fuente, no está en este
documento.

Léelo antes de escribir una herramienta. Está escrito para que no vuelvas a derivar lo que ya
se midió, y para que no prometas al visor algo que el backend no puede sostener.

---

## 0. Reparto de trabajo — quién entrega qué

| Pieza | Quién | Estado |
|---|---|---|
| Índice vectorial sobre los trozos curados | `kevdev04` | ⏳ en preparación |
| **Orquestador de agentes (Omnigent)** | **otro equipo — NO este repo** | ⏳ |
| Visor VR y Gate API de aprobación | `kevdev04/hacknation` | ✅ funcionando |
| **Este backend: datos, curación, procedencia, RAG y puente** | **nosotros** | ✅ |

**Este repositorio no entrega el orquestador.** Lo que entrega es el servicio del que el
orquestador se alimenta: el corpus curado, la puerta de procedencia, la recuperación con cita y
el puente de eventos hacia el visor.

El punto de enganche es **uno solo**:

```python
# agent_lab/runtime.py  — lo implementa quien hace el orquestador, no este repo
async def stream_discovery(query: str, query_id: str):
    """Generador asíncrono que emite los eventos de ar_vr_bridge/contract.py."""
```

En cuanto ese archivo exista y exporte esa función, `mode: "live"` deja de caer al simulador y
el visor consume el laboratorio real **sin cambiar una línea del visor ni del puente**. Todo lo
que hay que emitir está en `ar_vr_bridge/contract.py`; lo que el visor necesita de cada evento,
en la sección 5 de este documento.

---

## 1. Qué es este servicio, en una frase

Recuperación de literatura publicada y análisis estadístico sobre datos ya publicados, con
**procedencia verificable**: toda afirmación apunta a un documento aprobado por una persona y,
cuando lleva número, a la frase exacta donde está escrito.

Lo que **no** hace, y conviene que el orquestador no lo pida: no diseña ni propone secuencias
ni variantes de proteína, no escribe protocolos de laboratorio y no genera datos experimentales
nuevos. Detalle en [`ALCANCE.md`](ALCANCE.md).

> **Coordinación con el repo del visor.** `kevdev04/hacknation` propone **mutaciones puntuales
> a PETase** puntuadas con ESM-2. Eso es diseño de variantes y queda **fuera** del alcance de
> este repo. Los dos pueden convivir —una hipótesis generada allí entra aquí como hipótesis
> etiquetada como tal— pero la frontera tiene que quedar explícita en la entrega: este backend
> no avala la propuesta de variantes, solo la literatura y los datos publicados que se citen.

---

## 2. Datos reales disponibles

| Tabla | Filas | Estado | Para qué sirve |
|---|---|---|---|
| `workspace.lab.documents_curated` | **21 907** trozos / **3 424** docs | ✅ todos con `approved_by` | Corpus aprobado. Es la verdad contra la que se coteja la procedencia |
| `workspace.lab.documents_staging` | 4 945 | ✅ | Sin curar. **No citable** |
| `workspace.lab.pet_activity_ml` | **1 570** × 42 col. | ✅ | Vista lista para modelar (enzima × condición) |
| `workspace.lab.enzyme_features` | 213 | ✅ | Una fila por enzima, 33 columnas numéricas |
| `workspace.lab.mutant_stability` | **0** | ⚠️ **vacía** | Cualquier hipótesis que cite un `record_id` se rechaza hoy |
| `workspace.lab.research_record` | 3 y creciendo | ✅ **el puente ya escribe** | Toda consulta del visor, veredicto de hipótesis y aprobación deja traza |
| `workspace.lab.queries` | creciendo | ✅ **nueva** | Toda pregunta recibida, con su latencia, `has_evidence` y `doc_id` citados |

### El índice vectorial no es el corpus curado

```
workspace.lab.rag_v0_idx   ONLINE                          <- el que se usa HOY
  └── origen: workspace.lab.rag_v0 — 4 213 filas = 4 213 docs, UNA FILA POR DOCUMENTO

workspace.lab.rag_v1_idx   PROVISIONING_INITIAL_SNAPSHOT   <- el bueno, construyendose
  └── origen: workspace.lab.documents_curated — por TROZO (2 850 de 21 907 al 2026-10-03)
```

**El cambio a `rag_v1_idx` es una variable de entorno, no código:**

```bash
VS_INDEX=workspace.lab.rag_v1_idx uv run uvicorn ar_vr_bridge.app:app --port 8010
```

`retrieval.indice_por_trozos()` detecta solo que el origen es `documents_curated` y entonces el
`snippet` pasa a ser **el pasaje que disparó el acierto**, traído en la misma consulta: sin SQL
extra y sin aproximar. Probado contra el índice a medio construir: funciona y devuelve trozos
reales (470-709 ms). **Espera a que `ready: true`**: con 2 850 de 21 907 trozos la cobertura
todavía es parcial.

- **879** documentos indexados **no** están en `documents_curated`: nadie los aprobó.
- **90** documentos curados **no** están indexados: invisibles para la búsqueda.
- Al ser una fila por documento, el índice **no puede devolver el trozo que disparó el acierto**.

**El puente ya lo compensa:** `retrieval.search()` descarta toda cita cuyo `doc_id` no esté en
`documents_curated`. Medido: la mejor cita de «what increases thermostability» era
`openalex:W4385684008` (score 0.841) y **no está curada**; ya no se devuelve.

Cuando el índice se reconstruya sobre los trozos curados (lo prepara `kevdev04`), el filtro deja
de descartar nada y se queda como red de seguridad. No hay que quitarlo.

---

## 3. Presupuesto de tiempo, medido

| Camino | Latencia | Nota |
|---|---|---|
| Índice en frío | **2 054 ms** | Por eso existe `keep_warm` cada 45 s |
| `search()` en caliente | **p50 318 ms**, máx 1 919 ms | El filtro de curación va en memoria: no cuesta nada |
| `search(con_snippet=True)` | **p50 2 793 ms** | Una consulta SQL extra. Cabe en el bucle, **no** en la voz |
| Carga de `doc_id` aprobados | 3 281 ms | Se paga en el arranque, no en la primera pregunta |

**Regla:** la voz tiene 1,5 s. Usa `con_snippet=False` (por defecto) en el camino de voz y
`True` en el bucle de exploración, que dispone de 9-13 s.

### Calidad de recuperación — límite conocido

Con el umbral `SCORE_THRESHOLD = 0.70`, de 5 consultas realistas **2 quedaron por debajo** y
devolvieron «sin evidencia», incluida `LCC ICCG melting temperature` (0.612), que es un dato
central del proyecto. No subas el umbral para que pase: lo que falta es el índice sobre trozos.
Cuando el agente no tenga evidencia, **que lo diga**; no es un fallo, es el comportamiento
correcto.

---

## 4. Endpoints — qué puede llamar el orquestador

### URL real (Databricks App)

```
https://lab-bridge-7474652340191726.aws.databricksapps.com
```

El puente está desplegado como **Databricks App** (`lab-bridge`): URL HTTPS estable, corre al
lado de los datos y no depende de ningún portátil ni de la Wi-Fi. Medido desde la App:
**533 ms** en `/api/v1/ask`.

**Requiere `Authorization: Bearer <token>`.** Sin cabecera, Databricks responde `302` hacia
OAuth. El token vive en el servidor de quien llama —la gate—, **nunca en el bundle del
navegador**, que es justo lo que pedía el contrato del front.

```bash
curl -H "Authorization: Bearer $TOKEN" https://lab-bridge-7474652340191726.aws.databricksapps.com/health
```

El service principal de la app es `12912d7d-4987-459b-b4e9-b804a03c4079`, con `USE CATALOG`,
`USE SCHEMA` y `SELECT` sobre `workspace.lab`, más `MODIFY` sobre `queries` y `research_record`.

Para desplegar un cambio:

```bash
databricks sync . "/Workspace/Users/<tu-usuario>/lab-bridge" --full --profile hack
databricks apps deploy lab-bridge --source-code-path "/Workspace/Users/<tu-usuario>/lab-bridge" --profile hack
```



Base en desarrollo: `http://localhost:8000`. **El visor del equipo espera este puente en
`:8010`** (`BRIDGE_URL` en `gate/bridge.py`), porque su Gate API ocupa el `:8000`.

Contrato completo en [`API.md`](API.md); esquema en `ar_vr_bridge/contract.py`, que es la única
fuente de verdad.

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/api/v1/hypothesis` | **Puerta de entrada.** Admite o rechaza una hipótesis por su procedencia |
| `WS` | `/ws/explore` | Bucle completo en streaming. Lo consume el visor |
| `POST` | `/api/v1/explore` | El mismo bucle, de una pieza |
| `POST` | `/api/v1/ask` | Camino rápido de voz: solo RAG. Sin evidencia, `citations: []` |
| `GET` | `/api/v1/queries` | Qué se ha preguntado y qué corridas se cayeron |
| `POST` | `/api/v1/evidence` | **Pasajes listos para `evidence_span`.** El paso previo obligado a una hipótesis |
| `GET` | `/api/v1/documents/{doc_id}` | El documento curado entero, por secciones |
| `POST` | `/api/v1/approve/{approval_id}` | Respuesta a una aprobación humana |

### Cómo se alimenta una hipótesis

El lab **no acepta preguntas abiertas para pivotarlas**: acepta hipótesis que ya traen su
respaldo. Una sin `respaldo` se rechaza por `respaldo_presente`.

```json
POST /api/v1/hypothesis
{
  "statement": "...", "prediction": "...",
  "variables": ["temperature_c", "activity"],
  "respaldo": [{"doc_id": "europepmc:35382549",
                "evidence_span": "la frase EXACTA, >= 40 caracteres",
                "value": 85.8, "unit": "C"}]
}
```

Devuelve `verdict` (`ADMITIDA` / `ADMITIDA_CON_AVISOS` / `RECHAZADA`), los `checks` con su valor
y umbral, y un `receipt_hash` reproducible. Criterio completo en
[`VERIFICABILIDAD.md`](VERIFICABILIDAD.md).

**El bucle correcto:** `POST /api/v1/evidence` → elige 3-5 pasajes de **fuentes distintas** →
`POST /api/v1/hypothesis` con esos `doc_id` y `evidence_span` *sin tocar un carácter* → recibo.
Nunca escribas tú la frase: cópiala del pasaje.

**Ojo con la pertinencia.** La puerta prueba trazabilidad, **no** que el documento sostenga la
hipótesis: una frase real de un documento irrelevante sale `ADMITIDA`. Quien filtra por
pertinencia es el RAG, con su score. Por eso el respaldo se toma de `/api/v1/evidence` y no
buscando por palabra clave.

**Lo que hace fallar a un agente, por orden de frecuencia esperada:**

1. Parafrasear la frase en vez de copiarla → `span_literal` falla. **Copia literal, no resumas.**
2. Atribuirla al `doc_id` equivocado → el recibo te dice en qué documento sí está. Corrígelo y reenvía.
3. Citar un `record_id` → hoy **siempre** falla: `mutant_stability` está vacía.
4. Nombrar una variable que no es columna de `pet_activity_ml` → `variables_existen`.

---

## 4.bis La traza en `research_record`

Lo que entra por el puente deja rastro en Databricks. Antes no: el front podía conectar
perfectamente y no aparecer en ninguna tabla, lo que hacía imposible comprobar una prueba de
conexión y dejaba sin cumplir el *«shared research record»* que pide el brief.

| Cuándo | `kind` | `from_agent` → `to_agent` |
|---|---|---|
| Llega una consulta al bucle (WS o REST) | `handoff` | `viewer` → `bridge` |
| Se resuelve una hipótesis en la puerta | `decision` | `submitted_by` → `gate` |
| Una persona aprueba o rechaza | `approval` | `human:visor` → `safety_agent` |

Tres reglas, porque esto vive en una demo en vivo: **no bloquea** (va en segundo plano, el visor
no espera a Databricks), **no rompe** (si el warehouse cae se pierde la traza y se sigue) y **no
inunda** (se anotan decisiones, no cada nodo: ~3 escrituras por sesión).

Se apaga con `RESEARCH_RECORD=0`.

```sql
SELECT ts, session_id, from_agent, to_agent, kind, summary
  FROM workspace.lab.research_record ORDER BY ts DESC LIMIT 20;
```

---

## 5. Lo que el visor consume, y por tanto no se puede romper

`gate/bridge.py` de `kevdev04/hacknation` ya consume `WS /ws/explore` y devuelve decisiones a
`POST /api/v1/approve/{id}`. Depende de estos campos:

| Campo | Uso en el visor |
|---|---|
| `node.agent_generated` | **Solo se somete a revisión humana lo escrito por un modelo.** Si lo marcas mal, el gate revisa lo que no toca o deja pasar lo que sí |
| `node.confidence` | Por debajo de su umbral, pide revisión |
| `node.label` / `node.detail` | Texto del panel y de dónde extrae el token de mutación |
| `node.citation_ids` → `citations[]` | Procedencia: usa `doc_id`, `doi`, `url` y **`snippet`** |
| `validation.verdict` y `checks[]` | Muestra valor y umbral, no una aserción |
| `approval_id` | Devuelve la decisión al bucle |

**`snippet` ya se rellena** (antes iba siempre vacío) con el trozo curado que más términos
comparte con la consulta. Como el índice es de documento, **no es necesariamente el pasaje que
disparó el acierto**: si ningún trozo comparte términos, se devuelve vacío a propósito. Preferimos
vacío a un fragmento que parezca la evidencia sin serlo. Con el índice sobre trozos, pasa a ser
el trozo real.

---

## 6. Lo que NO existe todavía

| Pieza | Estado | Consecuencia |
|---|---|---|
| `agent_lab/runtime.py` con `stream_discovery(query, query_id)` | ❌ no existe — **lo implementa el equipo del orquestador, no este repo** (ver §0) | Mientras tanto `mode: "live"` cae al simulador, y lo anuncia |
| `mutant_stability` poblada | ❌ 0 filas | La vía numérica de la puerta no se puede usar |
| `research_record` escribiéndose | ✅ hecho | El puente anota `handoff` / `decision` / `approval` |
| Índice sobre trozos curados | ⏳ lo prepara `kevdev04` | Hasta entonces, filtro + snippet aproximado |

La caída a simulador **ya no es silenciosa**: si pides `live` y no hay orquestador, sale un
`stage` que lo dice. Y el `DoneEvent.latency_ms` **se mide**; antes traía 9 000 ms fijos mientras
el reloj real marcaba 13 639.

---

## 7. Herramientas de Python que ya puedes registrar

| Callable | Qué hace |
|---|---|
| `agent_lab.tools.search_staging` | Búsqueda por texto en `documents_staging` (sin curar) |
| `agent_lab.tools.fetch_sources` | Amplía el corpus desde las 5 fuentes abiertas |
| `agent_lab.tools.staging_stats` | Conteos del corpus |
| `ar_vr_bridge.registro.anotar` | Deja traza en `research_record` sin bloquear ni romper |
| `agent_lab.procedencia.verificar` | La puerta, sin red: recibe corpus y registros como datos |
| `agent_lab.procedencia_fuente.cargar_*` | Lee de Databricks solo lo citado |
| `ar_vr_bridge.retrieval.search` | RAG con filtro de curación y `snippet` opcional |

---

## 8. Trampas de los datos que ya costaron tiempo

No las vuelvas a descubrir. Detalle en [`CURACION.md`](CURACION.md) y
[`EXTRACCION_MUTANTES.md`](EXTRACCION_MUTANTES.md).

- **El DOI de una estructura PDB es el del artículo que la describe.** Deduplicar por DOI sin
  más perdió 201 de 301 estructuras. Nunca colapses `doc_type = structure` ni `prediction`.
- **943 artículos están en Europe PMC y OpenAlex a la vez**; `retrieval` ya colapsa por DOI.
- `Δ Tm`, «increase of», «higher than» son **incrementos**, no valores absolutos.
- Guiones y comillas Unicode: `LCC‐ICCG` (U+2010) y `PETase’s` (U+2019) son lo mismo que sus
  equivalentes ASCII. La puerta normaliza ambos; copiar el pasaje por JSON suele convertirlos.
- El dataset de actividad está **desbalanceado (29% positivos)**: se reporta macro-F1, nunca
  accuracy, y con la partición `cv_split` publicada, no una aleatoria.

---

## 9. Cómo comprobar que todo esto sigue siendo cierto

```bash
uv run python -m unittest discover -s tests -v     # 104 pruebas, sin red ni Databricks
uv run uvicorn ar_vr_bridge.app:app --port 8010    # el puerto que espera el visor
```
