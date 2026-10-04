# Guía de curación — corpus PETasa

Todo lo que necesitas para limpiar los datos, crear el índice RAG y entrenar los modelos.
Reto: *Agentic Scientific Discovery* (Databricks × Hack-Nation). Nicho: **enzimas que degradan PET**.

Pregunta científica del lab:
> ¿Qué propiedades de una PET hidrolasa predicen su actividad a 60 °C, y puede un lab de
> agentes encontrarlas con menos evaluaciones que un cribado exhaustivo?

**Alcance:** curación de literatura publicada y modelado estadístico sobre datos ya publicados.
No se diseñan secuencias ni se proponen modificaciones biológicas. Lee
[`ALCANCE.md`](ALCANCE.md) antes de empezar.

---

## 1. Acceso a Databricks

```bash
brew tap databricks/tap && brew install databricks
brew trust databricks/tap                       # si Homebrew bloquea el tap
databricks auth login --host https://dbc-19f58290-50fb.cloud.databricks.com --profile hack
databricks current-user me --profile hack       # verifica
```

Tu usuario (`cameron.malfoy@gmail.com`) ya está dado de alta con permisos de lectura y escritura
sobre `workspace.lab`. Acepta la invitación del correo o entra directamente al workspace con esa cuenta.

- **Workspace:** `https://dbc-19f58290-50fb.cloud.databricks.com`
- **Catálogo.esquema:** `workspace.lab` (el catálogo `hackathon` no existe en este workspace)
- **Volume:** `/Volumes/workspace/lab/raw` — payloads originales, inmutables
- **SQL warehouse:** `Serverless Starter Warehouse` (`b03ebf16c4226f7b`); arranca solo al primer query
- **Modelos disponibles:** LLM `databricks-gpt-oss-120b`, `databricks-llama-4-maverick`,
  `databricks-meta-llama-3-3-70b-instruct`. Embeddings `databricks-gte-large-en`, `databricks-bge-large-en`.
  **No hay modelos de Claude en el workspace.**

Para explorar desde la terminal con las skills de Databricks ya instaladas en el repo:

```bash
uv sync
uv run python -c "from data_pipeline.databricks_io import Databricks; print(Databricks().sql('SHOW TABLES IN workspace.lab'))"
```

---

## 2. Qué hay ya cargado

### 2.1 Corpus documental (para el RAG)

`workspace.lab.documents_staging` — **sin limpiar, ese es tu trabajo**.

Total: **4 945 filas**.

| source | Filas | Qué trae |
|---|---|---|
| `europepmc` | 1 126 | 1 026 artículos y 100 preprints. **712 traen texto completo con tablas** en `full_text` |
| `openalex` | 3 299 | 2 692 artículos, 402 preprints, **102 tesis** (`doc_type='thesis'`) y 103 datasets, con conteo de citas |
| `pdb` | 301 | Estructuras cristalinas. `metadata.mutations` trae **las mutaciones declaradas**, más secuencia y UniProt |
| `alphafold` | 120 | Predicciones de estructura por accesión UniProt, con pLDDT |
| `zenodo` | 99 | 26 datasets, 61 publicaciones y 10 de software. Incluye el de actividad ya procesado (ver 2.2) |

Columnas: `doc_id, source, source_id, doc_type, title, authors, year, doi, url, license,
is_open_access, abstract, full_text, raw_path, metadata, content_hash, query, fetched_by, fetched_at`.

`raw_path` apunta al JSONL con el payload original en el Volume, por si necesitas un campo que no normalicé.

### 2.2 Tablas numéricas (para tus algoritmos) — **ya listas**

Procesadas desde Zenodo `10.5281/zenodo.15417757` (CC-BY-4.0, Norton-Baker et al. 2025):

| Tabla | Filas | Qué es |
|---|---|---|
| `workspace.lab.enzyme_features` | 213 | Una fila por enzima: 33 columnas numéricas calculadas de la secuencia |
| `workspace.lab.pet_activity` | 1 570 | Una fila por **(enzima, condición medida)**: pH, temperatura, sustrato, actividad |
| `workspace.lab.pet_activity_ml` | 1 570 | **Vista unida, lista para modelar** |

`pet_activity_ml` es exactamente la tabla que te faltaba: cada fila es un ensayo y las columnas
son propiedades medidas.

```
enzyme_id | ph  | temperature_c | substrate           | seq_length | molecular_weight | isoelectric_point |
DP003     | 7.5 | 60            | crystalline_powder  | 259        | 28336.05         | 5.41              |
  ... charge_ph7 | aromaticity | instability_index | gravy | hydrophobic_fraction | helix/turn/sheet_fraction |
  ... aa_A ... aa_Y (20 columnas de composición) | cv_split | activity | is_active
```

**El paso que faltaba (secuencia → columnas numéricas) ya está resuelto** en
`data_pipeline/datasets/pet_activity.py` con Biopython `ProteinAnalysis`: peso molecular, punto
isoeléctrico, carga a pH 7, aromaticidad, índice de inestabilidad, GRAVY, fracción hidrofóbica,
fracciones de estructura secundaria y composición de los 20 aminoácidos.

El tar de Zenodo también está en `/Volumes/workspace/lab/raw/zenodo/15417757/p740.tar.gz`
con **1 679 estructuras AF2 en PDB**, por si quieres features 3D o usarlas en el visor.

---

## 3. Lo que tienes que hacer

### Paso A — Limpiar `documents_staging` → `documents_curated`

`documents_curated` ya existe, con `chunk_id` como clave primaria y Change Data Feed activo
(requisito del índice Delta Sync).

Problemas reales que tiene el corpus, verificados:

1. **Duplicados entre fuentes.** Medido sobre las 4 945 filas: hay **3 352 DOIs distintos** y
   **1 016 DOIs repetidos en 2 222 filas** (881 de ellos entre Europe PMC y OpenAlex).
   Deduplica por `doi` (ya viene normalizado: minúsculas, sin prefijo) y **prefiere la fila de
   Europe PMC cuando tenga `full_text`**. Quedan **387 filas sin DOI**: para esas, usa
   `content_hash` o el título.

   > **Trampa: el DOI de una estructura PDB es el del artículo que la describe.** 98 DOIs
   > distintos cubren 237 estructuras PDB, y 47 DOIs se comparten entre estructuras diferentes.
   > Si deduplicas por DOI sin más, un artículo "absorbe" sus estructuras y una estructura
   > absorbe a las demás: la primera versión del pipeline perdió **201 de 301** (y el registro
   > propio de 68 de las 95 con mutaciones declaradas, que es justo el dato valioso).
   > **Deduplica solo entre registros del mismo tipo** (artículo, preprint, tesis, publicación)
   > y **nunca** colapses `doc_type = structure` ni `prediction`: cada estructura es un registro
   > distinto aunque comparta el DOI. El criterio vive en `data_pipeline/curation/curar.py`
   > (`LITERATURA`, `NUNCA_DEDUP`). Lo mismo vale si colapsas por DOI al recuperar.
2. **Ruido temático.** La consulta fue amplia a propósito (`PETase OR "PET hydrolase" OR
   "poly(ethylene terephthalate) hydrolase"`): trae cutinasas, MHETasas y reciclaje en general.
   Filtra o etiqueta; no borres sin registrar el criterio.
3. **Chunking.** `full_text` trae las secciones marcadas con `## Título`. Trocea por sección,
   objetivo 500–1 000 tokens con solape de ~15%. **No partas las tablas**: ahí están los valores
   de Tm y actividad de las variantes.
4. **Licencias.** Conserva `license`. No todo el acceso abierto de Europe PMC es CC-BY, y el
   informe final tiene que poder declararlo.
5. **Sin texto aprovechable.** **638 filas** tienen el resumen por debajo de 50 caracteres y
   ningún texto completo. No aportan al RAG: descártalas o márcalas.

### Paso B — Crear el índice de AI Search

El endpoint **`lab-vs` ya está creado y ONLINE** (id `f751f1e7-4b87-4b82-a43c-919e2048fcb9`).
No tienes que crearlo: solo el índice encima.

Índice Delta Sync sobre `documents_curated`, con embeddings gestionados:

- Columna de texto: `text`
- Clave primaria: `chunk_id`
- Modelo de embeddings: `databricks-gte-large-en`
- Columnas a devolver: `chunk_id, doc_id, title, year, doi, url, source, section, license,
  relevance, language, subtopic, subtopic_secondary, evidence_type, enzyme`
- **Filtro por defecto recomendado** para las consultas de los agentes:
  `relevance IN ('nucleo_pet_enzima','enzima_plasticos_sin_pet','estructura') AND language = 'en'`
  (14 004 trozos de 2 139 documentos). La periferia (reciclaje general, microplásticos y salud,
  ingeniería de proteínas sin PET) sigue en la tabla como contexto, pero ensucia las respuestas
  si no se filtra. `databricks-gte-large-en` es solo inglés: por eso el filtro de idioma.

Cuando exista, el equipo lo conecta a los agentes con:

```bash
ug mcp add --agents claude --names "vector-search:workspace.lab,uc-functions:workspace.lab"
```

### Paso C — Tus algoritmos sobre `pet_activity_ml`

**Escribe tu código en `algorithms/`**, no dentro de `data_pipeline/` (eso es ingesta).
El pipeline de curación del paso A va en `data_pipeline/curation/`.

Lo que ya puedes correr sin esperar a nadie:

- **Paso 1 (IDA\*, selección de variables):** objetivo `activity` (regresión) o `is_active`
  (clasificación). 33 features candidatas.
- **Paso 2 (red neuronal + genético):** optimiza hiperparámetros o la selección de features.
- **Paso 3 (alfa-beta):** elige el modelo robusto. Aquí es especialmente pertinente: el dataset
  es pequeño y desbalanceado.

**Cuidados que impone este dataset concreto:**

- **Usa `cv_split`, no un split aleatorio.** La columna viene del paper original. Un split
  aleatorio filtra información: la misma enzima aparece en varias condiciones y acabaría
  en train y test a la vez. **Agrupa por `enzyme_id`.**
- **Desbalance 29/71.** Solo 453 de 1 570 mediciones tienen actividad > 0. Usa **macro-F1**,
  no accuracy. Un modelo que diga siempre "no activa" acierta el 71%.
- **Los vacíos ya no existen.** En el CSV original, celda vacía significaba *condición no medida*,
  no actividad cero. Ya las excluí: las 1 570 filas son mediciones reales. No las rellenes con 0.
- **Fija el tipo de tarea a mano.** `activity` tiene pocos valores distintos y muchos ceros, así
  que un detector automático de tipo puede confundirla con una variable categórica. Usa
  `activity` para regresión e `is_active` para clasificación, de forma explícita.
- **Reporta media ± σ en validación cruzada**, con early stopping, y declara el gap train/val.
  El brief puntúa el rigor (15%), y el Safety Agent del lab usa justo esas métricas como umbral.

### Paso D — Extraer `mutant_stability` desde la literatura

La tabla `workspace.lab.mutant_stability` está vacía y es **el cuello de botella que mide el
reto**: no existe ninguna base de datos limpia de "mutante de PETasa → termoestabilidad". Ese
dato está disperso en las tablas de ~239 artículos de acceso abierto.

Columnas: `record_id, enzyme, uniprot, mutations, metric, value, unit, conditions, doc_id,
evidence_span, extracted_by, verified, created_at`.

El agente la llena con citas obligatorias; tú verificas una muestra y marcas `verified`.
**Mide cuánto tardas en curar 10 a mano:** esa cifra, frente a lo que tarda el agente, es la
"aceleración medida" que vale el 20% de la nota.

---

## 4. Reglas que no se negocian

- **Nada entra al RAG sin pasar por `documents_curated`.** Los agentes escriben en
  `documents_staging` con `fetched_by = 'agent:<nombre>'`; la aprobación humana es tuya.
- **Toda afirmación lleva cita.** `doc_id` y `evidence_span` son obligatorios en `mutant_stability`.
- **No borres de `documents_staging`.** Marca y filtra, para que cada decisión se pueda reconstruir.
- **Registra en `research_record`** cualquier decisión de curación relevante.

---

## 5. Referencias

- Brief del reto: lo tiene el equipo fuera del repo; pídelo si lo necesitas
- Conectores y esquema: `data_pipeline/README.md`
- Alcance y límites del proyecto: `docs/ALCANCE.md`
- Dataset de actividad: Zenodo [10.5281/zenodo.15417757](https://doi.org/10.5281/zenodo.15417757), CC-BY-4.0
- Enzima de referencia: IsPETase, UniProt `A0A0K8P6T7`, estructura PDB `5XJH` (1.54 Å)
- AI Search: usa la skill `databricks` ya instalada en el repo (`.claude/settings.json`)
