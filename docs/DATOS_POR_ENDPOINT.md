# Qué información saca el agente de cada endpoint

Referencia campo a campo, con **respuestas reales** capturadas contra la App, no ejemplos
inventados. Si buscas un dato y no sabes de dónde sale, empieza por la tabla de abajo.

**Base:** `https://lab-bridge-7474652340191726.aws.databricksapps.com`
**Todas las llamadas necesitan** `Authorization: Bearer <token>`. Sin la cabecera, Databricks
responde `302` hacia OAuth y parece que el puente está caído.

---

## Dónde está cada cosa

| Lo que necesitas | Endpoint | Campo |
|---|---|---|
| **Título** de un paper | `/api/v1/evidence`, `/api/v1/documents/{id}`, `/api/v1/ask` | `title` |
| **Texto** para citar (`evidence_span`) | `/api/v1/evidence` | `evidence_span` |
| **Texto completo** de un paper | `/api/v1/documents/{id}` | `passages[].evidence_span` |
| **Secciones** de un paper | `/api/v1/documents/{id}` | `sections` |
| Buscar **dentro** de un paper | `/api/v1/evidence` con `doc_id` | — |
| **DOI / enlace** a la fuente | todos | `doi`, `url` |
| **Año** y **fuente** | todos | `year`, `source` |
| **Comprobaciones** de una hipótesis | `/api/v1/hypothesis` | `checks[]`, `failures`, `warnings` |
| **Puntuación** de relevancia | `/api/v1/ask` | `score` |
| Qué se ha preguntado | `/api/v1/queries` | — |
| **PDF** | ✗ **no existe en el corpus** | — |

> **No hay PDF.** El corpus guarda el **texto completo ya troceado por secciones** de 707
> documentos. Para «mira en el paper esta parte» se usa `/api/v1/evidence` con `doc_id` y
> `section`, o `/api/v1/documents/{doc_id}` para leerlo entero. Es más útil que un PDF: viene
> troceado, con sección, y listo para citar literal.

---

## `POST /api/v1/evidence` — pasajes para citar

Lo que **más va a usar el agente**: es el único sitio de donde sale el texto que la puerta
acepta como evidencia.

```bash
curl -s -X POST "$BASE/api/v1/evidence" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"melting temperature of LCC ICCG variant","num_results":2}'
```

Cada elemento de `passages[]` trae:

| Campo | Qué es | Ejemplo real |
|---|---|---|
| `chunk_id` | Id del trozo; clave primaria del índice | `europepmc:39979673#3` |
| `doc_id` | **El que se manda a `/api/v1/hypothesis`** | `europepmc:39979673` |
| `source` | `europepmc` · `openalex` · `pdb` · `alphafold` · `zenodo` | `europepmc` |
| `title` | Título del documento | `Enhancing thermostability of Moloney murine leukemia virus…` |
| `year` | Año de publicación | `2025` |
| `doi` | DOI | `10.1186/s40643-025-00845-0` |
| `url` | Enlace a la fuente, resoluble | `https://europepmc.org/article/MED/39979673` |
| `section` | Sección del paper de la que sale | `Resumen` · `Methods` · `Results` |
| `evidence_span` | **El texto, literal.** Se copia sin tocar un carácter | *(el pasaje entero)* |
| `overlap` | Términos de la consulta presentes en el pasaje | `3` |

Y en la raíz: `count`, `doc_ids` (las fuentes distintas halladas), `latency_ms`.

**Parámetros:** `query` (1-500), `num_results` (1-30, por defecto 8), `doc_id` (busca dentro de
ese documento), `section` (filtra por sección).

Como máximo **3 pasajes por documento**, para que el respaldo venga de varias fuentes.

> **Comprueba el `title` antes de citar.** El ejemplo de arriba es real y devolvió un paper
> sobre *transcriptasa inversa de leucemia murina* para una consulta sobre PET: el corpus se
> construyó con una consulta amplia y la búsqueda semántica sobre «thermostability» arrastra
> material de otros dominios. **La puerta comprueba trazabilidad, no pertinencia**: una frase
> real de un documento irrelevante sale `ADMITIDA`. Filtrar por `title` y `section` es trabajo
> del agente.

---

## `GET /api/v1/documents/{doc_id}` — el paper entero

Lo más parecido a abrir el PDF. Devuelve **todos** los trozos curados con sus secciones.

```bash
curl -s "$BASE/api/v1/documents/europepmc:40617831" -H "Authorization: Bearer $TOKEN"
```

```json
{
  "schema_version": "1.0",
  "doc_id": "europepmc:40617831",
  "source": "europepmc",
  "title": "Harnessing protein language model for structure-based discovery of highly efficient and robust PET hydrolases.",
  "year": 2025,
  "doi": "10.1038/s41467-025-61599-z",
  "url": "https://europepmc.org/article/MED/40617831",
  "chunks": 32,
  "sections": [
    "Activity and thermostability measurements of discovered PETases.",
    "Benchmarking protein language models for protein discovery",
    "Characterizations of putative PET hydrolases",
    "Crystallization and structure determination of",
    "Differential scanning calorimetry",
    "Introduction", "Methods", "..."
  ],
  "passages": [ { "...igual que en /evidence..." } ]
}
```

`404` si el documento no está en `documents_curated` — es decir, si nadie lo aprobó. Eso es
deliberado: lo no curado no se cita.

**Para leer sólo una sección**, en vez de traerte los 32 trozos:

```bash
curl -s -X POST "$BASE/api/v1/evidence" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"melting temperature","doc_id":"europepmc:40617831","section":"calorimetry"}'
```

---

## `POST /api/v1/hypothesis` — la comprobación, campo a campo

Aquí sale **toda** la información de verificación. Cada `check` lleva su número y su umbral: se
enseña el valor, nunca una aserción.

| Campo | Qué es |
|---|---|
| `verdict` | `ADMITIDA` · `ADMITIDA_CON_AVISOS` · `RECHAZADA` |
| `admitted` | `false` sólo en `RECHAZADA` |
| `receipt_hash` | Hash reproducible: mismo input y mismo corpus, mismo hash |
| `checks[].name` | Qué se comprobó (`respaldo[0].span_literal`, `doc_aprobado`…) |
| `checks[].passed` | Si pasó |
| `checks[].value` / `threshold` | El número medido y el umbral |
| `checks[].detail` | **Por qué falló**, en texto |
| `checks[].fatal` | `false` = aviso, no tumba el veredicto |
| `failures` | Nombres de los checks fatales que fallaron |
| `warnings` | Avisos (p. ej. una sola fuente) |

**El `detail` es lo que permite corregir sin adivinar.** Por ejemplo, cuando la frase existe
pero en otro documento:

```json
{"name":"respaldo[0].span_literal","passed":false,"value":0.0,"threshold":1.0,
 "detail":"la frase no esta en europepmc:111; si aparece en: europepmc:40617831","fatal":true}
```

Lista completa de comprobaciones y sus límites en
[`VERIFICABILIDAD.md`](VERIFICABILIDAD.md); el bucle de dos pasos y cómo llevar el veredicto al
visor, en [`API.md`](API.md).

---

## `POST /api/v1/ask` — respuesta rápida con citas

Camino de voz (~0,5 s). Devuelve `answer`, `tts_text` (≤ 40 palabras), `has_evidence`,
`latency_ms`, `query_id` y `citations[]`.

Cada cita trae `id`, `doc_id`, `title`, `authors_short`, `year`, `doi`, `url`, `source`,
**`score`** y `snippet`:

```json
{"id":"c2","doc_id":"pdb:6JTT","title":"MHETase in complex with BHET","authors_short":"",
 "year":2020,"doi":"10.1021/acscatal.9b05604","url":"https://www.rcsb.org/structure/6JTT",
 "source":"pdb","snippet":"","score":0.708}
```

**Dos cosas que hay que saber de este endpoint:**

1. **`snippet` viene vacío hoy.** El índice actual (`rag_v0_idx`) es de un documento por fila y
   no devuelve el pasaje. Por eso **no se puede citar desde aquí**: para el `evidence_span` hay
   que ir a `/api/v1/evidence`. Cuando termine `rag_v1_idx` (sobre los trozos curados), el
   `snippet` será el pasaje que disparó el acierto.
2. **`score` sí está**, y es el único sitio donde sale la puntuación de relevancia. Por debajo
   de `0.70` el sistema responde `has_evidence: false` y **`citations` va vacío**: sin evidencia
   no se devuelve ni una fuente.

---

## `GET /api/v1/queries` — qué se ha preguntado

Auditoría. `query_id`, `query` (**tal cual llegó**, sin limpiar: las asperezas del transcriptor
son evidencia), `source` (`voice`/`text`/`agent`), `asked_by`, `asked_at`, `mode`
(`live`/`mock`), `language`, `answered`, `verdict`, `has_evidence`, `latency_ms`,
`citation_doc_ids`, `error`.

Parámetros: `limit` (tope 500), `since`, `asked_by`, `unanswered`.

`unanswered=true` devuelve las corridas que nunca se cerraron: una fila en `answered = false` una
hora después es un fallo localizable. Por eso se inserta **al recibir**, no al terminar.

---

## Lo que de verdad no se puede sacar

Para no perder tiempo buscándolo:

| No existe | Por qué | Alternativa |
|---|---|---|
| PDF de un paper | No se descargan PDF | Texto completo troceado: `/api/v1/documents/{id}` |
| `authors_short` poblado | Sólo viene en algunas fuentes | `title` + `doi` + `url` |
| `snippet` en `/ask` | El índice es por documento | `/api/v1/evidence` |
| Datos de `mutant_stability` | La tabla está **vacía** | Citar `doc_id` + `evidence_span`, sin `record_id` |
| Documentos sin curar | `documents_staging` no se expone | Sólo lo aprobado por una persona es citable |
