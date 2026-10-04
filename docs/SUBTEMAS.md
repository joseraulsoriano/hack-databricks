# Subtemas, relevancia y etiquetas por trozo

Cada fila de `workspace.lab.documents_curated` es un trozo. Estas columnas permiten navegar de lo
general a lo específico y filtrar el ruido. Se calculan en `data_pipeline/curation/etiquetar.py` y
`curar.py`; se añadieron a la tabla con `migrar_esquema.py`.

## Los 7 subtemas (tabla aprobada)

Salieron de explorar el corpus con dos métodos independientes (agrupamiento NMF sobre título y
resumen, y una lista de temas por palabras clave): `explorar_subtemas.py`.

| `subtopic` | Tema | Trozos |
|---|---|---|
| `productos_despolimerizacion` | Productos de despolimerización (MHET, TPA, BHET) | 2 082 |
| `ingenieria_estabilidad` | Ingeniería y estabilidad de enzimas | 1 704 |
| `biodegradacion_ambiente` | Biodegradación microbiana y microplásticos (ambiente) | 1 684 |
| `reciclaje_economia_circular` | Reciclaje, upcycling y economía circular | 1 249 |
| `mecanismo_simulacion` | Mecanismo y simulación (dinámica molecular, QM/MM) | 1 215 |
| `produccion_sistemas_celulares` | Producción y sistemas celulares (expresión, *display*, célula completa) | 1 160 |
| `descubrimiento_caracterizacion` | Descubrimiento y caracterización | 1 140 |
| *(NULL)* | Sin subtema | 11 673 |

**Por trozo y no por documento**: el 80 % de los documentos toca tres o más temas, y un trozo es
mucho más específico. `subtopic_secondary` lleva el segundo tema cuando pesa casi como el primero.

**Solo para el núcleo.** Los subtemas describen la literatura PET-enzima. En documentos periféricos
quedan en NULL: etiquetar con ellos una sección sobre lignina como "productos de despolimerización"
es un error que apareció en la primera versión.

## Cómo se asignan (y cuánto vale)

Léxico de patrones con peso por subtema; el título de la sección cuenta doble; se asigna el de
mayor puntuación si llega a un umbral (si no, NULL). Es determinista y auditable, **no es un modelo
ni está verificado contra etiquetas humanas.**

Contraste con el agrupamiento automático, que no usa mi léxico: para 8 de los 9 temas descubiertos
los trozos etiquetados coinciden entre **1,8 y 4,5 veces** más de lo esperable por azar; el subtema
mayoritario de un documento coincide con su tema descubierto en el **53 %** (azar 14 %, poner siempre
el más frecuente 21 %). Es una señal real pero moderada: sirve para **navegar y filtrar**, no como
verdad de referencia.

**Debilidades conocidas**
- `productos_despolimerizacion` tiende a sobre-asignarse (palabras como *monomer* salen en casi todo).
- El 53 % de los trozos queda sin subtema (periferia, estructuras, métodos genéricos, tablas sin tema).
- **Posible octavo subtema:** un grupo de ~99 documentos (5 % del núcleo) sobre la biología de
  *Ideonella sakaiensis*, la IsPETase y la MHETasa naturales no encaja en ninguno de los siete
  (el léxico los asigna a otros temas con 0,3× de coincidencia).

## Otras columnas

| Columna | Valores | Nota |
|---|---|---|
| `relevance` | `nucleo_pet_enzima`, `enzima_plasticos_sin_pet`, `plasticos_sin_enzima`, `fuera_de_alcance`, `estructura` | Heurística sobre título y resumen. El 44 % de los documentos menciona PET y enzima a la vez |
| `language` | `en`, `es`, `pt`, `de`, `fr`, `cjk`, `cirilico`, `desconocido` | Heurística por escritura y palabras frecuentes, **sin validar**. 127 documentos no están en inglés |
| `evidence_type` | `resumen`, `tabla`, `estructura`, `prediccion`, `introduccion`, `metodos`, `resultados`, `discusion`, `otro` | Deriva solo del nombre de la sección. El 49 % es `otro` (revisiones con secciones temáticas) |
| `enzyme` | lista de nombres | Solo nombres específicos (IsPETase, LCC, FAST-PETase…); se omite "PETase" a secas |

## Filtro por defecto del RAG

```sql
relevance IN ('nucleo_pet_enzima','enzima_plasticos_sin_pet','estructura') AND language = 'en'
```
13 997 trozos de 2 139 documentos. La periferia queda como contexto, no se borra.
