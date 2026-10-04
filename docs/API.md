# API del puente — cómo piden los datos la web y las gafas

Referencia de los endpoints que consumen el visor WebXR (Meta Quest 2), el cliente web y
cualquier otro cliente.

**Base en desarrollo:** `http://localhost:8000`
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
| `POST` | `/api/v1/ask` | **Camino rápido para la voz.** Solo RAG, < 1,5 s | ⏳ por implementar |

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

## `POST /api/v1/ask` — camino rápido para la voz ⏳

**Todavía no implementado.** Esta es su especificación, para quien lo construya.

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
