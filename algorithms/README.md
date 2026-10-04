# algorithms/

Modelos predictivos sobre `workspace.lab.pet_activity_ml` (1 570 filas × 42 columnas).

Esta carpeta es para selección de variables, modelos y comparaciones. La ingesta vive en
`data_pipeline/`; el pipeline de curación, en `data_pipeline/curation/`.

## La tabla

Una fila por **(enzima, condición medida)**. 213 enzimas × hasta 11 condiciones.

| Grupo | Columnas |
|---|---|
| Identidad | `enzyme_id`, `cv_split` |
| Condición del ensayo | `ph`, `temperature_c`, `substrate` |
| Descriptores de secuencia | `seq_length`, `molecular_weight`, `isoelectric_point`, `charge_ph7`, `aromaticity`, `instability_index`, `gravy`, `hydrophobic_fraction`, `helix/turn/sheet_fraction` |
| Composición | `aa_A` … `aa_Y` (20 columnas) |
| Objetivo | `activity` (regresión), `is_active` (clasificación) |

```python
from data_pipeline.databricks_io import Databricks
rows = Databricks().sql("SELECT * FROM workspace.lab.pet_activity_ml")
```

Sin Databricks, se regenera en local:

```bash
uv run python -m data_pipeline.datasets.pet_activity   # deja los CSV en data/datasets/
```

## Cuatro cosas que este dataset impone

1. **Agrupa por `enzyme_id`.** La misma enzima aparece en varias condiciones; un split aleatorio
   la pondría en train y test a la vez. Usa `cv_split`, que viene del paper original.
2. **Desbalance 29/71.** 453 de 1 570 mediciones tienen actividad > 0. Reporta **macro-F1**;
   un modelo que diga siempre "no activa" acierta el 71%.
3. **Fija el tipo de tarea a mano.** `activity` tiene pocos valores distintos y muchos ceros:
   un detector automático puede tomarla por categórica.
4. **No rellenes huecos con cero.** Las 1 570 filas son mediciones reales; las condiciones no
   medidas ya están excluidas.

## Correr los tres algoritmos

`pet_activity_pipeline.py` aplica los motores de `ia_generica` (IDA\*, genético + MLP, alfa-beta)
con las cuatro reglas de arriba ya puestas: partición por `cv_split`, exclusión de `enzyme_id`,
del otro objetivo y de `design_round`/`temporal_split`, tarea explícita y macro-F1.

```bash
uv run python -m algorithms.pet_activity_pipeline                    # pasos 1-3, objetivo is_active
uv run python -m algorithms.pet_activity_pipeline --pasos 1 --max-variables 3
uv run python -m algorithms.pet_activity_pipeline --objetivo activity
```

- Lee `workspace.lab.pet_activity_ml` y, si Databricks no responde, los CSV locales.
- Los motores (IDA\*, genético + MLP, alfa-beta) viven en `algorithms/ia_generica/` y son genéricos:
  sirven para cualquier CSV tabular. Se pueden sustituir con `--ia-generica RUTA` o `IA_GENERICA=RUTA`.
- Antes de los pasos corre dos baselines en condiciones idénticas: clase mayoritaria y bosque
  con todas las columnas.
- Resultados en `data/resultados/algorithms/pipeline_<objetivo>.json` (fuera de git).
- El paso 1 es el lento: unos 3,5 min con `--max-variables 3` y crece combinatoriamente.

## Qué reportar

Media ± desviación en validación cruzada, gap train/val, y la comparación baseline frente al
método propuesto **en condiciones idénticas**. Esas cifras alimentan los umbrales del Safety
Agent del laboratorio y el criterio de rigor del reto.

Registra los experimentos en MLflow.
