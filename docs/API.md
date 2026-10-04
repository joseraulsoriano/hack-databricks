# API del puente — cómo piden los datos la web y las gafas

Referencia de los endpoints que consumen el visor WebXR (Meta Quest 2), el cliente web y
cualquier otro cliente.

**Base real (Databricks App):** `https://lab-bridge-7474652340191726.aws.databricksapps.com` — HTTPS, requiere `Authorization: Bearer <token>`
**Base en desarrollo:** `http://localhost:8000`
**Puerto que espera el visor del equipo:** `:8010` (su Gate API ocupa el `:8000`)
**Base en gafas:** una URL HTTPS (túnel o Databricks App). WebXR exige contexto seguro.

```bash
uv run uvicorn ar_vr_bridge.app:app --host 0.0.0.0 --port 8000 --reload
```

El esquema de los objetos (`Node`, `Edge`, `Citation`, `Validation`) está en
`ar_vr_bridge/contract.py`, que es la **única fuente de verdad**. Este documento describe el
transporte; aquel, la forma de los datos.

---

## Resumen

| Método | Ruta | Para qué | Estado |
|---|---|---|---|
| `GET` | `/` | Sirve el visor WebXR | ✅ |
| `GET` | `/health` | Comprobación de vida | ✅ |
| `WS` | `/ws/explore` | **Bucle completo en streaming.** El camino principal | ✅ |
| `POST` | `/api/v1/explore` | El mismo bucle, respuesta de una pieza | ✅ |
| `POST` | `/api/v1/approve/{approval_id}` | Responder a una aprobación humana | ✅ |
| `POST` | `/api/v1/hypothesis` | **Puerta de entrada.** Admite o rechaza una hipótesis por su procedencia | ✅ |
| `POST` | `/api/v1/ask` | **Camino rápido para la voz.** Solo RAG, < 1,5 s | ✅ |
| `GET` | `/api/v1/queries` | Las preguntas recibidas y su resultado | ✅ |
| `POST` | `/api/v1/evidence` | **Pasajes curados para construir una hipótesis** | ✅ |
| `GET` | `/api/v1/documents/{doc_id}` | El documento curado entero, por secciones | ✅ |

---

## Quién usa qué

```
Gafas Quest 2 ──┬─ WS  /ws/explore   el grafo 3D crece evento a evento
                └─ POST /api/v1/ask  la voz responde en menos de 1,5 s

Web (portátil) ─── WS  /ws/explore   mismo cliente, sin modo inmersivo

Otro cliente ───── POST /api/v1/explore   una sola llamada, sin WebSocket
```

**Regla de diseño:** el bucle completo tarda de 9 a 13 segundos. La voz no puede esperar eso,
así que usa `/api/v1/ask`, que solo consulta el RAG. El grafo 3D se alimenta en paralelo del
WebSocket. Los dos caminos son independientes y se lanzan a la vez.

---

## `GET /health`

```json
{ "status": "ok", "schema_version": "1.0" }
```

---

## `WS /ws/explore` — el camino principal

El cliente abre el socket y manda **un** mensaje de texto con JSON:

```json
{ "query": "¿Qué aumenta la termoestabilidad de las PET hidrolasas?", "mode": "live" }
```

| Campo | Tipo | Notas |
|---|---|---|
| `query` | string | La pregunta, tal cual la dijo o escribió la persona |
| `mode` | `"live"` \| `"mock"` | `mock` usa el simulador. Si el laboratorio no está disponible, `live` cae a `mock` solo |
| `query_id` | string | Opcional. Si no lo mandas, el servidor genera uno |

El servidor responde con **una secuencia de eventos**, uno por mensaje, cada uno un JSON con
el campo `event`. Llegan en este orden:

| `event` | Cuándo | Qué hacer en el cliente |
|---|---|---|
| `stage` | Al cambiar de fase | Actualizar texto de progreso y barra (`progress`, de 0 a 1) |
| `node` | Por cada nodo | Añadirlo al grafo en la altura que marca `layer` |
| `edge` | Por cada arista | Unir dos nodos ya presentes. Si falta alguno, ignorar |
| `citations` | Tras recuperar | Pintar la lista de fuentes con su `url` |
| `answer` | Al concluir | Titular, conclusión, justificación y `tts_text` para la voz |
| `validation` | Tras validar | Veredicto `PASS`/`WARN`/`FAIL` con los checks que lo sostienen |
| `approval_request` | Si el Safety Agent lo pide | **Congelar el grafo** y mostrar el panel de decisión |
| `done` | Al terminar | Latencia real y la siguiente pregunta que investigaría el lab |
| `error` | Si algo falla | Mostrar el mensaje y rehabilitar la entrada |

Ejemplo de dos eventos consecutivos:

```json
{"event":"stage","query_id":"q_8f2c","stage":"retrieving","message":"Buscando evidencia","progress":0.2}
{"event":"node","query_id":"q_8f2c","node":{"id":"e1","type":"evidence","label":"Joo et al. 2018","layer":1,"confidence":0.91,"agent_generated":false,"citation_ids":["c1"],"props":[]}}
```

### Responder a una aprobación por el mismo socket

Cuando llega `approval_request`, el cliente manda por el **mismo** socket:

```json
{ "approval_id": "ap_3c1d", "decision": "approve" }
```

Valores: `approve` o `reject`. El servidor no lo trata como pregunta nueva. Si nadie responde
en 120 segundos, la decisión queda en `timeout` y el agente no escribe nada.

### Detalles de conexión

- Una pregunta por socket es lo normal, pero puedes mandar varias en serie: los eventos de cada
  una llevan su `query_id`.
- Si el socket se cae, el servidor termina la sesión. El cliente debe reconectar y repreguntar.
- `wss://` cuando la página se sirve por HTTPS, que es el caso en las gafas.

---

## `POST /api/v1/explore` — respuesta de una pieza

Mismo bucle, pero acumulado. **Tarda de 9 a 13 segundos**, así que no sirve para la voz.
Úsalo en pruebas o desde clientes sin WebSocket.

```bash
curl -X POST http://localhost:8000/api/v1/explore \
  -H 'Content-Type: application/json' \
  -d '{"query":"¿Qué papel cumple la MHETasa?","mode":"mock"}'
```

Devuelve `schema_version`, `query_id`, `status`, `answer`, `nodes`, `edges`, `citations`,
`validation` y `latency_ms`.

---

## `POST /api/v1/approve/{approval_id}` — aprobación por HTTP

Alternativa al WebSocket, por si la decisión se toma desde otra pantalla.

```bash
curl -X POST 'http://localhost:8000/api/v1/approve/ap_3c1d?decision=approve'
```

```json
{ "status": "ok", "approval_id": "ap_3c1d", "decision": "approve" }
```

Si el identificador no existe o ya venció, devuelve `"status": "unknown_approval"`.

---

## `POST /api/v1/hypothesis` — puerta de entrada del lab

El lab no recibe preguntas abiertas: recibe **hipótesis que ya traen su respaldo**, y esta puerta
decide si entran. El veredicto es una función determinista de los datos curados, no un juicio:
el mismo input sobre el mismo corpus devuelve el mismo `receipt_hash`.

Criterio completo, lista de comprobaciones y límites en **[`VERIFICABILIDAD.md`](VERIFICABILIDAD.md)**.

**Nunca escribas tú el `evidence_span`.** Pídelo antes a
[`/api/v1/evidence`](#post-apiv1evidence--pasajes-para-el-agente) y cópialo sin tocar un
carácter: la puerta lo coteja literal. Este es el bucle de dos pasos que debe seguir el
orquestador.

```bash
BASE=https://lab-bridge-7474652340191726.aws.databricksapps.com

# PASO 1 — conseguir pasajes reales, de varias fuentes
curl -s -X POST "$BASE/api/v1/evidence" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"thermostability engineering of PET hydrolases","num_results":8}'

# PASO 2 — mandar la hipótesis con esos pasajes, copiados tal cual
curl -s -X POST "$BASE/api/v1/hypothesis" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "statement": "La ingeniería de proteínas aumenta la termoestabilidad de las PET hidrolasas sin perder actividad",
    "prediction": "Las variantes descritas como termoestables conservan actividad medida a 60 C en pet_activity_ml",
    "variables": ["temperature_c", "activity"],
    "respaldo": [
      {"doc_id": "europepmc:40617831",
       "evidence_span": "To elucidate the molecular basis of Kb PETase'"'"'s enhanced thermostability, we performed comparative all-atom molecular dynamics (MD) simulations with LCC and Is PETase, representing thermophilic and mesophilic PET hydrolases, respectively."},
      {"doc_id": "<otro doc_id del paso 1, de OTRA fuente>",
       "evidence_span": "<su evidence_span, literal>"}
    ],
    "submitted_by": "agent:insight"
  }'
```

Medido contra la App: paso 1 en **2,6 s** (6 pasajes de 4 fuentes), paso 2 en **1,3 s**.

| Campo de `respaldo` | Notas |
|---|---|
| `doc_id` | Obligatorio. Tiene que existir en `documents_curated` y tener `approved_by` |
| `evidence_span` | Obligatorio, ≥ 40 caracteres. Se coteja **literal** contra el texto del documento |
| `value` | Opcional. Si viene, el número debe estar escrito en `evidence_span` |
| `record_id` | Opcional. Fila de `mutant_stability`; debe tener `verified = true` |

**Respuesta**

```json
{
  "schema_version": "1.0",
  "receipt_hash": "5091b7670ecc7470",
  "verdict": "ADMITIDA_CON_AVISOS",
  "admitted": true,
  "checks": [{"name":"respaldo[0].span_literal","passed":true,"value":1.0,"threshold":1.0,"detail":"","fatal":true}],
  "failures": [],
  "warnings": ["fuentes_distintas: toda la evidencia viene de: europepmc"],
  "latency_ms": 412
}
```

`verdict` es `ADMITIDA`, `ADMITIDA_CON_AVISOS` o `RECHAZADA`; `admitted` es `false` sólo en la
última. Cada comprobación que falla aparece en `failures` por nombre, y su `detail` dice por qué
—incluido el caso en que la frase existe pero en **otro** documento, que es el error de
atribución típico.

### Del veredicto al visor y a las gafas

La puerta **no dibuja nada**: devuelve un recibo. Quien lo lleva a la web y a las Quest es el
orquestador, emitiendo los eventos de `ar_vr_bridge/contract.py` desde
`agent_lab/runtime.py::stream_discovery` — en cuanto ese archivo exista, `mode: "live"` deja de
caer al simulador **sin tocar el visor ni el puente**.

El recibo se traduce así, campo a campo:

| Del recibo / la hipótesis | Evento al visor | Por qué importa |
|---|---|---|
| `statement` | `node` con `type: "hypothesis"`, `agent_generated: true` | El visor dibuja con borde discontinuo lo escrito por un modelo, y **sólo eso** se somete a revisión humana |
| `respaldo[].doc_id` | `citations` + `citation_ids` en el nodo | El panel enseña la fuente con su `url` y su `snippet` |
| `checks[]` | `validation.checks` | Cada fila lleva `value` y `threshold`: se muestra el número, no una aserción |
| `verdict` | `validation.verdict` (`PASS`/`WARN`/`FAIL`) | `RECHAZADA` → `FAIL`; con avisos → `WARN` |
| `receipt_hash` | en `node.props` | Permite recalcular el veredicto y comparar |

```python
# agent_lab/runtime.py — lo implementa el equipo del orquestador
async def stream_discovery(query: str, query_id: str):
    yield StageEvent(query_id=query_id, stage="hypothesizing", progress=0.45,
                     message="Formulando hipótesis")
    yield CitationsEvent(query_id=query_id, citations=citas)      # de /api/v1/evidence
    yield NodeEvent(query_id=query_id, node=Node(
        id="h1", type="hypothesis", label=recibo_statement[:48],
        detail=statement, layer=LAYER["hypothesis"],
        agent_generated=True,                                     # <- lo que el gate revisa
        citation_ids=[c.id for c in citas],
        props=[{"receipt_hash": recibo["receipt_hash"]}]))
    yield ValidationEvent(query_id=query_id, validation=Validation(
        verdict="WARN" if recibo["warnings"] else "PASS",
        checks=[Check(**c) for c in recibo["checks"]]))
    yield DoneEvent(query_id=query_id)                             # la latencia la mide el puente
```

Una hipótesis `RECHAZADA` **no se emite como nodo**: se corrige y se reenvía. El recibo dice en
`failures` qué comprobación falló y en `detail` por qué —incluido en qué otro documento sí está
la frase—, así que el agente puede arreglarla sin adivinar.

Del lado del visor esto ya está resuelto: `gate/bridge.py` consume estos eventos, convierte los
nodos `agent_generated` en candidatos de revisión y devuelve la decisión humana a
`POST /api/v1/approve/{approval_id}`.

---

Si el warehouse no responde, la respuesta es `RECHAZADA` con `corpus_inaccesible`: sin poder
cotejar, nada se admite a ciegas.

---

## `POST /api/v1/ask` — camino rápido para la voz

Implementado y medido de extremo a extremo por HTTP: **0,50-0,89 s en caliente**, 3,3 s en frío
(índice sin calentar). Cumple el presupuesto de 1,5 s con el servidor en marcha.

**Petición:** `query` (1-500), `num_results` (1-10, por defecto 5), y opcionales `query_id`
(para correlacionar con una corrida de `/ws/explore`), `language` (BCP-47), `asked_by`, `source`
(`voice` | `text` | `agent`).

**Respuesta:** `schema_version`, `query_id`, `answer`, `citations`, `has_evidence`, `tts_text`
(≤ 40 palabras) y `latency_ms`.

### `has_evidence` manda

Cuando es `false`, `citations` va **vacío** y `answer` dice que no hay evidencia. Es la línea
que separa esto de un chatbot: una respuesta fluida sin cita es peor que no responder.

`answer` es **extractivo**: el pasaje citado más su atribución, nunca prosa generada. Así toda
afirmación es rastreable a un `doc_id` por construcción. Si algún día se genera con un LLM, hay
que volver a demostrar ese respaldo.

Errores: `422` si `query` falta o se sale de rango, `503` si el índice no responde.

---

## `GET /api/v1/queries` — qué se ha preguntado

Toda pregunta se guarda en `workspace.lab.queries` **al recibirla**, con `answered = false`, y la
fila se cierra al terminar. Ese orden es el punto: si una pregunta tumba el laboratorio, su fila
se queda abierta y se puede encontrar. Escribir solo al terminar perdería justo esa.

| Parámetro | Notas |
|---|---|
| `limit` | 50 por defecto, tope 500 |
| `since` | ISO-8601; solo `asked_at` posterior |
| `asked_by` | Filtra por revisor |
| `unanswered` | Solo `answered = false`: las corridas que se cayeron |

```bash
curl -s "http://localhost:8010/api/v1/queries?unanswered=true"
```

Se apaga con `QUERIES_LOG=0`.

Consulta **solo** el índice vectorial y devuelve una respuesta corta con su cita. No lanza
hipótesis, ni experimento, ni validación: por eso cabe en el presupuesto de la voz.

**Petición**

```json
{ "query": "¿Qué papel cumple la MHETasa?", "num_results": 5 }
```

**Respuesta**

```json
{
  "query_id": "q_8f2c",
  "answer": "La MHETasa completa la degradación iniciada por la PETasa…",
  "tts_text": "Versión de 40 palabras o menos para la voz",
  "citations": [
    {"id":"c1","doc_id":"europepmc:29374183","title":"…","authors_short":"Joo et al.",
     "year":2018,"doi":"10.1038/…","url":"https://…","snippet":"…","score":0.83}
  ],
  "latency_ms": 940,
  "has_evidence": true
}
```

**Reglas que debe cumplir**

- **Presupuesto: 1,5 segundos** desde la petición hasta el primer byte. Si se pasa, hay que
  recortar `num_results` o acortar la generación.
- **Streaming de tokens.** Devolver `text/event-stream` para mandar el texto a ElevenLabs según
  sale, sin esperar a la respuesta completa.
- **`has_evidence: false`** cuando el índice no devuelve nada por encima del umbral. En ese caso
  la voz debe decir que no tiene evidencia, no improvisar. Es lo que separa este sistema de un
  modelo general.
- Toda afirmación con su `doc_id`.

**Fuente de datos**

| | |
|---|---|
| Índice | `workspace.lab.rag_v0_idx` (resúmenes sin curar) |
| Endpoint | `lab-vs` |
| Embeddings | `databricks-gte-large-en` |
| Clave | `chunk_id` |

Cuando la curación termine, se cambia a `rag_v1_idx` sobre `documents_curated`. **Es una línea
de configuración; el cliente no se entera.**

---

## Errores

| Código | Cuándo |
|---|---|
| `422` | El JSON no cumple el esquema. FastAPI detalla el campo |
| `500` | Fallo del laboratorio o de Databricks. El WebSocket manda `error` en vez de cortar |

El visor debe tolerar que falte un evento: si llega un `edge` cuyos nodos no existen, se ignora
en vez de romper el render.


---

## `POST /api/v1/evidence` — pasajes para el agente

`/api/v1/ask` devuelve `doc_id` y título pero **no el texto**, y la puerta de procedencia exige
el `evidence_span` *literal*. Sin pasajes, el agente tendría que inventarse la frase y la puerta
se la rechazaría: construir una hipótesis era imposible por construcción. Esto lo resuelve.

```bash
curl -X POST "$BASE/api/v1/evidence" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"thermostability engineering of PET hydrolases","num_results":8}'
```

| Campo | Notas |
|---|---|
| `query` | 1-500 caracteres |
| `num_results` | 1-30, por defecto 8 |
| `doc_id` | Opcional: busca **dentro** de ese documento — «mira en este paper esta parte» |
| `section` | Opcional: filtra por sección (`Introduction`, `Methods`, `Results`…) |

Devuelve `passages[]` con `chunk_id`, `doc_id`, `section`, `url` y **`evidence_span`**, que se
pega *tal cual* en una hipótesis. Como máximo 3 pasajes por documento, para que el respaldo
venga de varias fuentes y no de una sola.

No está en el camino de voz, así que puede pagar la consulta SQL que trae el texto. Tampoco
escribe en `queries`: no es una pregunta del laboratorio.

## `GET /api/v1/documents/{doc_id}` — el documento entero

Todos los trozos curados de un documento, con sus secciones. **No hay PDF en el corpus**; esto
es el texto que sí hay (707 documentos traen texto completo). Sirve para que una persona lo lea
completo en vez de fiarse de un fragmento. `404` si el documento no está curado.
