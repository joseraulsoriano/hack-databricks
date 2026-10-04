# hack-databricks — Laboratorio Científico Agéntico

Reto **Agentic Scientific Discovery** · 7th Global AI Hackathon · Databricks × Hack-Nation × MIT Club

Un laboratorio de agentes que acelera el descubrimiento de **enzimas que degradan PET**
(plástico): busca evidencia citada, propone hipótesis, corre experimentos computacionales,
valida con rigor estadístico y muestra el proceso en realidad virtual sobre Meta Quest 2.

> **Pregunta científica:** ¿Qué propiedades de una PET hidrolasa predicen su actividad a 60 °C,
> y puede un laboratorio de agentes encontrarlas con menos evaluaciones que un cribado exhaustivo?

## Alcance

Este proyecto es **recuperación de literatura y análisis estadístico sobre datos ya publicados**.

Lee artículos de acceso abierto, los organiza con sus citas y licencias, calcula descriptores
fisicoquímicos agregados de secuencias publicadas (Biopython) y entrena modelos que relacionan
esos descriptores con actividad medida y publicada por terceros.

**No diseña ni genera secuencias de proteínas, no propone modificaciones genéticas, no incluye
protocolos de laboratorio húmedo y no produce datos experimentales nuevos.** El cuello de botella
que ataca es documental: consolidar lo que cientos de artículos ya publicaron por separado.

Detalle completo de límites, controles y validación pendiente en **[`docs/ALCANCE.md`](docs/ALCANCE.md)**.

---

## Por qué este nicho

El Nobel de Química 2024 premió la predicción y el diseño de proteínas. AlphaFold resolvió
*qué forma* tienen; el cuello de botella hoy es *qué variante probar primero*. Para las PET
hidrolasas no existe una base de datos limpia que relacione mutación con termoestabilidad:
ese dato está disperso en cientos de artículos. Ese es el cuello de botella que ataca este lab.

---

## Arquitectura

```
Fuentes abiertas          Unity Catalog (workspace.lab)        Agentes (Omnigent)      Meta Quest 2
──────────────────        ─────────────────────────────        ──────────────────      ────────────
Europe PMC    ─┐
OpenAlex       │  conectores   documents_staging  ──curación──> documents_curated
RCSB PDB       ├──────────────>      │                                 │ Delta Sync
AlphaFold DB   │                     │                                 ▼
Zenodo        ─┘                     │                          índice AI Search ──> Literature agent ─┐
                                     │                                                                 │
                              enzyme_features  ─┐                                      Insight agent ──┤
                              pet_activity      ├─> pet_activity_ml ──> Experiment runner ─────────────┤
                                                ┘                                      Analysis agent ─┤
                                                                                       Safety agent ───┤
                                     research_record  <──── cada handoff, decisión y aprobación ───────┘
                                            │
                                            └──> WebSocket ──> visor VR: el grafo crece en vivo
```

---

## Estado

| Pieza | Estado |
|---|---|
| Esquema `workspace.lab` y Volume | ✅ creados |
| Conectores (5 fuentes) | ✅ 4 945 documentos en `documents_staging` |
| Tablas numéricas para modelar | ✅ `enzyme_features` (213), `pet_activity` (1 570), vista `pet_activity_ml` |
| Endpoint de Vector Search `lab-vs` | ✅ creado y ONLINE |
| Curación → `documents_curated` | ✅ 21 907 trozos de 3 424 documentos, con subtema, idioma y relevancia (`docs/SUBTEMAS.md`) |
| Índice AI Search sobre `documents_curated` | ⏳ `rag_v1_idx` construyéndose; se activa con `VS_INDEX` |
| Grafo de agentes Omnigent | ⏳ |
| Puerta de procedencia `POST /api/v1/hypothesis` | ✅ admisión determinista con recibo reproducible |
| Puente API + visor Quest 2 | ⏳ |

---

## Arranque rápido

```bash
git clone git@github.com:joseraulsoriano/hack-databricks.git && cd hack-databricks
uv sync
cp .env.example .env

brew tap databricks/tap && brew install databricks
databricks auth login --host https://dbc-19f58290-50fb.cloud.databricks.com --profile hack
databricks current-user me --profile hack

uv run python -c "from data_pipeline.databricks_io import Databricks; print(Databricks().sql('SHOW TABLES IN workspace.lab'))"
```

Omnigent (gestionado en el workspace):

```bash
uv tool install "omnigent[databricks]"
omnigent login dbc-19f58290-50fb.cloud.databricks.com/omnigent
```

---

## Pruebas

```bash
uv run python -m unittest discover -s tests -v    # 104 pruebas, sin red ni Databricks
```

Cada caso fija un error real que se encontró al revisar los datos (la trampa del DOI en PDB, un
incremento leído como valor absoluto, un polímero confundido con una enzima…). Si vuelve, falla.

## Estructura

```
data_pipeline/        Conectores e ingesta  → docs en data_pipeline/README.md
  connectors/         europepmc, openalex, pdb, alphafold, zenodo
  datasets/           pet_activity: secuencias → tabla numérica (Biopython)
  databricks_io.py    SQL y subida al Volume
algorithms/           Modelos predictivos sobre pet_activity_ml
agent_lab/            Agentes Omnigent y herramientas de datos
ar_vr_bridge/         Puente API + WebSocket y esqueleto del visor WebXR
sql/                  Esquema de Unity Catalog
docs/                 Arranque por persona:
  PROMPT_AGENTE.md      orquestación con Omnigent (30% de la nota)
  PROMPT_CURACION.md    curación de datos, índice RAG y modelos
  PROMPT_VISOR.md       visor Quest 2 y voz con ElevenLabs
  CURACION.md           tablas, índice y cuidados estadísticos
  API.md                endpoints que consumen la web y las gafas
  VISOR.md              contrato de eventos y presupuesto de rendimiento
  CASOS_DE_PRUEBA.md    14 casos para probar el bucle completo
  ALCANCE.md            qué hace y qué no hace el proyecto
  VERIFICABILIDAD.md    cómo se justifica cada afirmación y qué admite el lab
  CONTEXTO_AGENTE.md    resumen medido del que se alimenta el orquestador
  DATOS_POR_ENDPOINT.md qué información saca el agente de cada endpoint, campo a campo
  FLUJO.md              de las gafas a los agentes, al RAG y de vuelta con la aprobación

```

## Ramas

| Rama | Uso |
|---|---|
| `main` | Rama general, siempre estable |
| `test` | Pruebas e integración |
| `algoritmos` | Curación de datos y modelos predictivos |

---

## Equipo

5 personas: orquestación de agentes · curación y algoritmos · RAG y endpoints ·
experimento y validación · interfaz AR/VR.

## Datos y licencias

Europe PMC (licencia por artículo) · OpenAlex (CC0) · RCSB PDB (CC0) · AlphaFold DB (CC-BY-4.0) ·
Zenodo [10.5281/zenodo.15417757](https://doi.org/10.5281/zenodo.15417757) (CC-BY-4.0,
Norton-Baker et al. 2025). La licencia se conserva por documento en `documents_staging.license`.
