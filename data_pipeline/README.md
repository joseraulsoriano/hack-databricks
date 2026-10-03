# data_pipeline: corpus PETasa

Descarga literatura, estructuras y datasets sobre PETasa y los deja en Unity Catalog
para que el equipo de curación los limpie antes de crear el índice RAG.

## Flujo

```
conectores ──> /Volumes/workspace/lab/raw/runs/<run_id>/   (payload original, inmutable)
           └─> workspace.lab.documents_staging             (filas normalizadas, sin deduplicar)
                         │  curación (algoritmos + revisión humana)
                         ▼
               workspace.lab.documents_curated  ──Delta Sync──>  índice AI Search
```

Los agentes también escriben en `documents_staging` (`agent_lab.tools.fetch_sources`, con
`fetched_by = 'agent:<nombre>'`). Nada llega al RAG sin pasar por `documents_curated`.

## Fuentes

| source | Qué trae | Texto | Licencia |
|---|---|---|---|
| `europepmc` | Artículos y preprints biomédicos | Resumen + texto completo (incl. tablas) del subconjunto OA | Por artículo (`license`) |
| `openalex` | Artículos, **tesis** (`doc_type='thesis'`) y preprints, con conteo de citas | Resumen | Por obra |
| `pdb` | Estructuras cristalinas con **mutaciones declaradas** (`metadata.mutations`), secuencia y UniProt | Ficha | CC0 |
| `alphafold` | Predicciones de estructura por accesión UniProt (pLDDT) | Ficha | CC-BY-4.0 |
| `zenodo` | Datasets (actividad de PET hidrolasas, Tsuboyama 2023 de estabilidad) | Descripción + lista de archivos | Por registro |

Consulta por defecto: `PETase OR "PET hydrolase" OR "poly(ethylene terephthalate) hydrolase"`.
Es amplia a propósito: incluye cutinasas y otras hidrolasas que la curación debe filtrar o etiquetar.

## Uso

```bash
uv run python -m data_pipeline.ingest --apply-schema --download-structures --sink databricks   # corpus completo
uv run python -m data_pipeline.ingest --sources europepmc --limit 20                            # prueba local
```

## Qué queda para curación

- **Duplicados entre fuentes**: el mismo artículo aparece en Europe PMC y OpenAlex. Clave: `doi`,
  y `content_hash` para copias exactas. Preferir la fila de Europe PMC cuando tiene `full_text`.
- **Relevancia**: la consulta trae trabajos de otras hidrolasas o de reciclaje en general.
- **Chunking**: `full_text` trae secciones marcadas con `## Título`; las tablas van en línea y son
  donde aparecen los valores de Tm de las variantes (fuente para `mutant_stability`).
- **Licencias**: conservar `license` en `documents_curated`; no todo el OA de Europe PMC es CC-BY.
- Cada fila apunta a su payload original en `raw_path` (JSONL con `doc_id` + `payload`).
