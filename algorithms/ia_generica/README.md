# ia_generica — procesamiento de datasets

Tres algoritmos de IA aplicados a cualquier dataset tabular (CSV con encabezados).
Las columnas de texto y los valores vacíos se manejan solos. Si la tarea es clasificación o regresión se detecta sola.

| Paso | Script | Algoritmo | Qué hace con el dataset |
|---|---|---|---|
| 1 | `seleccion_variables.py` | IDA\* (búsqueda en árbol) | Encuentra el **conjunto mínimo de columnas** que conserva la precisión y guarda un CSV reducido |
| 2 | `mlp_genetico.py` | Red neuronal + algoritmo genético | **Entrena una red neuronal** y ajusta sus hiperparámetros |
| 3 | `seleccion_robusta.py` | Minimax con poda alfa-beta | **Elige el modelo más robusto**: modelo (MAX) contra el peor escenario de datos (MIN) |

Archivos de apoyo: `datos.py` (carga, preprocesamiento y modelos), `ida_estrella.py` y `alfa_beta.py` (motores).

## Uso

```bash
pip install -r requirements.txt

# 1. Reducir columnas (acepta perder como máximo 0.01 de score)
python3 seleccion_variables.py datos.csv --objetivo Clase --guardar reducido.csv

# 2. Red neuronal sobre el dataset reducido
python3 mlp_genetico.py reducido.csv --objetivo Clase --salida res_mlp

# 3. Comparar modelos (incluida la red del paso 2) frente a ruido, huecos, pocos datos y etiquetas erróneas
python3 seleccion_robusta.py reducido.csv --objetivo Clase --mlp-config res_mlp/mejor_individuo.json
```

Cada paso también funciona por separado. Usa `--help` en cada script para ver todas las opciones.

## Datos con filas repetidas por unidad

Si tu dataset tiene **varias filas de la misma unidad** (una enzima medida en 11 condiciones, un
paciente con varias visitas, una tienda con varios meses), una partición al azar deja esa unidad
en train y en test a la vez y **el score sale inflado**. Los tres scripts aceptan:

| Opción | Para qué |
|---|---|
| `--grupos COL` | La unidad que no se puede partir. La partición train/test y la validación cruzada se hacen por grupo |
| `--cv-split COL` | Fold ya asignado a cada fila (partición fija publicada con los datos). Manda sobre `--cv` |
| `--excluir COL...` | Columnas que no son variables: identificadores, metadatos y **otros objetivos** |

`--excluir` importa más de lo que parece: si predices una columna y en la tabla queda otra derivada
de ella, el modelo la copia y da un score perfecto que no significa nada.

```bash
python3 seleccion_variables.py datos.csv --objetivo is_active --tarea clasificacion \
    --excluir activity --grupos enzyme_id --cv-split cv_split
```

Los modelos se guardan con el preprocesamiento incluido. Para usarlos con datos nuevos:
```python
from joblib import load
modelo = load("resultados_robustez/modelo_robusto.joblib")
predicciones = modelo.predict(df_nuevo)   # df_nuevo con las mismas columnas
```

## Notas
- **Paso 1:** el número de combinaciones crece rápido. Si tarda demasiado, usa `--max-variables`, `--muestra` (filas usadas en la búsqueda) o una `--tolerancia` mayor.
- **Tipo de tarea:** se detecta solo, pero un objetivo entero con pocos valores distintos es ambiguo (¿clases o cantidades?). Si importa, pasa `--tarea`.
- **Paso 3:** la poda ahorra más entrenamientos si pones primero (`--modelos`) el modelo que esperas que sea mejor.
