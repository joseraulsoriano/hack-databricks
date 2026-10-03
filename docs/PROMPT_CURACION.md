# Prompt de arranque — persona de curación y algoritmos

Pega esto en Claude Code (o tu agente) **desde la raíz del repo**, en la rama `algoritmos`.
El repo ya trae instalado el plugin oficial de Databricks, así que tendrás las skills de
Unity Catalog, AI Search, MLflow y Model Serving disponibles.

---

```
Trabajo en el reto "Agentic Scientific Discovery" (Databricks × Hack-Nation, 24 h).
Mi parte: curación de datos, índice RAG y modelos predictivos. Lee docs/CURACION.md antes
de tocar nada; ahí está el detalle del workspace y de las tablas.

CONTEXTO
- Workspace Databricks: https://dbc-19f58290-50fb.cloud.databricks.com (perfil CLI: hack)
- Esquema: workspace.lab    Volume: /Volumes/workspace/lab/raw
- Nicho: enzimas que degradan PET (PETasa). Pregunta: qué propiedades de una PET hidrolasa
  predicen su actividad a 60 °C.
- Ya cargado por mi compañero:
  * documents_staging: ~4 900 documentos sin limpiar (Europe PMC con texto completo,
    OpenAlex incl. tesis, PDB con mutaciones declaradas, AlphaFold, Zenodo)
  * enzyme_features (213), pet_activity (1 570), vista pet_activity_ml  <- listas para modelar
  * documents_curated, mutant_stability, research_record: creadas y vacías

TAREAS, en este orden

1. Explora y perfila documents_staging. Cuantifica: duplicados por doi, filas sin texto
   aprovechable, distribución por source/doc_type/year. No borres nada todavía.

2. Escribe el pipeline de curación staging -> documents_curated:
   - deduplica por doi (normalizado), prefiriendo Europe PMC cuando tenga full_text;
     usa content_hash para copias exactas sin doi
   - filtra o etiqueta el ruido temático (la consulta fue amplia: trae cutinasas y
     reciclaje en general)
   - trocea full_text por sección (viene marcado con '## Título'), 500-1000 tokens con
     ~15% de solape, SIN partir tablas (ahí están los valores de Tm y actividad)
   - conserva license, doi, url, year y section en cada chunk
   - clave primaria: chunk_id = '<doc_id>#<n>'
   Deja el pipeline como script reproducible, no como notebook suelto.

3. Crea el índice de AI Search (Delta Sync) sobre documents_curated:
   endpoint lab-vs, texto en 'text', clave chunk_id, embeddings databricks-gte-large-en.
   El endpoint tarda en aprovisionarse: lánzalo lo primero, en paralelo con el paso 2.

4. Modelos sobre workspace.lab.pet_activity_ml (1 570 filas, 33 features, objetivo
   'activity' en regresión o 'is_active' en clasificación):
   - USA la columna cv_split del dataset original y agrupa por enzyme_id. Un split
     aleatorio filtra información porque la misma enzima aparece en varias condiciones.
   - El dataset está desbalanceado 29/71: reporta macro-F1, nunca accuracy.
   - Entrena baseline y modelo propuesto en condiciones idénticas, con early stopping,
     y reporta media ± desviación en validación cruzada más el gap train/val.
   - Registra los experimentos en MLflow.

5. Documenta en docs/ el esquema final, el índice y las métricas obtenidas, con las
   decisiones de curación y su criterio.

REGLAS
- Nada entra a documents_curated sin criterio explícito y registrado.
- No borres filas de documents_staging: marca y filtra.
- Toda afirmación científica necesita doc_id de origen.
- Conserva la licencia por documento: no todo el acceso abierto es CC-BY.
- Trabaja en la rama algoritmos y haz commits pequeños.

Empieza por el paso 1 y enséñame el perfil antes de escribir el pipeline.
```

---

## Comandos útiles

```bash
uv sync                                          # instala dependencias
databricks auth login --host https://dbc-19f58290-50fb.cloud.databricks.com --profile hack

# consulta rápida desde Python
uv run python -c "from data_pipeline.databricks_io import Databricks; \
  print(Databricks().sql('SELECT source, count(*) FROM workspace.lab.documents_staging GROUP BY 1'))"

# ampliar el corpus con otra consulta
uv run python -m data_pipeline.ingest --sources europepmc --limit 200 --sink databricks

# regenerar las tablas numéricas
uv run python -m data_pipeline.datasets.pet_activity --sink databricks
```
