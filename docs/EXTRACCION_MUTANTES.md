# Extracción de `mutant_stability`

Qué hace `data_pipeline/curation/extraer_mutantes.py`, qué garantiza y qué **no** garantiza.
Alcance: datos ya publicados; no se diseñan ni proponen variantes (ver `ALCANCE.md`).

## Revisión humana con criterio fijo

Objetivo: que ni el juicio libre de la persona ni la etiqueta del extractor decidan. El detalle
completo está en el encabezado de `data_pipeline/curation/revisar_mutantes.py`.

```bash
uv run python -m data_pipeline.curation.curar
uv run python -m data_pipeline.curation.extraer_mutantes
uv run python -m data_pipeline.curation.revisar_mutantes --exportar [--muestra 40 --semilla 42]
#   -> data/resultados/curacion/revision_mutantes.csv   (lo que se revisa)
#   -> data/resultados/curacion/filas_no_elegibles.csv  (lo que no, con su motivo)
uv run python -m data_pipeline.curation.revisar_mutantes --aplicar revision_mutantes.csv --revisor "Tu Nombre"
uv run python -m data_pipeline.curation.extraer_mutantes \
    --desde data/resultados/curacion/mutant_stability_revisada.json --solo-verificadas --aprobar "Tu Nombre"
```

**Qué se revisa lo decide una regla.** Solo filas con enzima y sin "respectively" (hoy 156 de
299 filas). Con `--muestra N` se sortea una muestra estratificada por confianza con semilla fija
(reproducible). Las demás no se publican: completar el sujeto de una fila sin enzima es otro trabajo.

**Cuatro preguntas de sí/no por fila**, y la decisión se deriva de las respuestas:

| Pregunta | Si es `n` | Se salva con |
|---|---|---|
| `q1` el número está tal cual en la frase | `valor_no_respaldado` | `valor_corr` (debe aparecer en la frase) |
| `q2` la frase nombra esa enzima/variante y el valor es suyo | `enzima_equivocada` | `enzima_corr` |
| `q3` la métrica es correcta (Tm absoluta / dTm incremento) | `metrica_equivocada` | `metrica_corr` |
| `q4` condiciones: `s` estándar o ninguna, `a` especial y anotada, `n` especial sin precisar | `condiciones_no_precisables` | no se salva: se descarta |

Todas bien → `ok`. Alguna en `n` con su corrección → `corregir`. Sin ella → `descartar`.
Las cuatro preguntas se responden completas o ninguna: un CSV con una fila a medias se rechaza
entero.

**Ayudas que no deciden.** `contraste` compara cada Tm con la de otros artículos para la misma
enzima y variante (`DISCREPA` si difiere más de 5 °C). Detecta valores raros, **no confirma**
nada: muchos artículos repiten el valor de uno original, por eso informa cuántos llevan una
referencia. `referencia_en_frase` marca las frases que citan otro trabajo; que no la tengan no
prueba que sea una medición propia.

**La etiqueta de confianza no aparece en el CSV** (`--mostrar-confianza` para verla): es una
opinión del extractor sin medir y empuja a aprobar sin leer. Se usa después, en
`revision_resumen.json`, para medir la precisión real por nivel con su cota inferior de Wilson
al 95 %. Con esa cifra, no con la etiqueta, se decide qué nivel se publica.

**Lo que no resuelve.** Con una sola persona revisando el criterio no se puede comprobar como
repetible; el único remedio es un segundo revisor sobre la misma muestra. Tampoco elimina el
anclaje: la fila ya trae enzima y valor rellenados. Un modo ciego (leer la frase sin ver la
propuesta) queda pendiente.

- `--exportar` no sobrescribe un CSV que ya tenga respuestas (`--forzar` para empezar de cero).
- Solo lo que una persona deja `ok` o `corregir` pasa a `verified = true`; las corregidas quedan como
  `human:<revisor> (corregido de agent:regex_extractor)`, con sus respuestas y motivos guardados.
- Una versión sin verificar **nunca** pisa en Databricks una fila ya verificada (`MERGE` condicionado).
- `record_id` es estable (hash del contenido): repetir la extracción no duplica filas.

## Cómo se corre solo la extracción

```bash
uv run python -m data_pipeline.curation.curar            # genera data/resultados/curacion/chunks.jsonl
uv run python -m data_pipeline.curation.extraer_mutantes --revisar 20 --confianza baja
uv run python -m data_pipeline.curation.extraer_mutantes --aprobar "Tu Nombre"   # escribe en Databricks
```

Sin `--aprobar` no se escribe nada en Databricks. Con `--aprobar` se hace un `MERGE` por
`record_id` (se puede repetir sin duplicar). Publicar directo desde la extracción deja todas las
filas con `verified = false`; solo `--desde …revisada.json --solo-verificadas` publica verificadas.
La aprobación queda en `research_record`.

## Qué es

Reglas deterministas, no un modelo. Trabaja por oración sobre los chunks que no son tablas:
busca "Tm" / "melting temperature" seguido de un valor en °C y atribuye la fila a la enzima
más cercana por delante. `extracted_by` es `agent:regex_extractor`: **no es una persona**.

## Qué garantiza

- Toda fila trae `doc_id` y `evidence_span` (la oración completa), y la oración **contiene el
  valor extraído**. Se audita automáticamente en cada corrida.
- Tm solo entre 20 y 120 °C. Los incrementos van aparte como `dTm`.

## Errores que ya se corrigieron (no reintroducir)

| Error | Regla |
|---|---|
| `Δ Tm = 24.9` leído como Tm | El símbolo Δ marca incremento → `dTm` |
| `Tm 30 °C higher than X` leído como valor | "higher/lower … than" tras el valor → `dTm` |
| `(TM)` = *triple mutant* | Sin valor tras la etiqueta no hay fila |
| Tm del PET, no de la enzima | Se descarta si el plástico está más cerca que cualquier enzima |
| Comparadores como sujeto ("compared to wild-type X", "with respect to", "in addition to") | Esa enzima no cuenta como sujeto |
| "respectively" | Se marcan en `baja`: el orden no es fiable |
| "85.8 °C for LCC" | El nombre tras `for` manda sobre el anterior |
| Variantes confundidas con la base | Se conserva el sufijo (`LCC-ICCG-NM`, `IsPETase-Cat`, `CaPETase M9`) |
| Guiones Unicode (`LCC‐ICCG`, U+2010) | Se normalizan sin cambiar la evidencia |
| Tm de un **polímero** (PCL, PBSA, "T g de −43 °C y T m de 89 °C", "modulus") | Lista de polímeros ampliada y ventana local alrededor del valor. El filtro por *oración entera* quitaba datos de enzimas ("PET T g", "low-crystallinity PET"): no usarlo |
| Rangos ("a range of Tm of 21.66", "ranged from 36.4 to 80.1") | No son un Tm |
| "Tm **increase of** 31 °C" leído como valor absoluto | Verbo de cambio + `of`/`by` → `dTm`; con `to` es absoluto |
| Valores que dependen del medio (Ca²⁺, NaCl, MeCN, "sin solvente", fuerza iónica) | Una `alta` pasa a `media` y se anota `medido con: …`. Las condiciones del ensayo **no** se extraen como dato |
| La enzima "ligada" al valor por una frase larga | El nombre buscado no puede cruzar otro Tm ni un verbo de cambio |

## Cómo leer `confianza` (ordena la revisión, no la sustituye)

- **alta** — enzima concreta, ligada al valor sin límite de cláusula, con mutaciones, wild-type
  o nombre de variante.
- **media** — enzima genérica ("PETase"), pareja emparejada por orden, o valor medido con
  condiciones del ensayo que lo cambian (iones, solvente, sal).
- **baja** — sin enzima, aproximado, incremento, "respectively", valor tras una modificación o
  enzima no ligada con certeza.

Quien escribió las reglas revisó a mano todas las filas `alta` y buena parte de las `baja`
contra su evidencia, y así se encontraron los errores de la tabla. Aun así, **ninguna se puede
dar por verificada sin una persona**: la regla puede atribuir mal una enzima que no figura en
su lista de nombres.

## Límites conocidos

- Solo detecta nombres de enzima de su lista (`ENZIMA`). Si el sujeto es otra enzima, la fila
  queda sin enzima y en `baja` (cerca de la mitad de las filas).
- Si la enzima se menciona **después** del valor ("Tm de 30 °C superior a la de X"), no se atribuye.
- **No lee tablas.** Cada tabla aplanada que menciona Tm y mutaciones o enzimas se lista en
  `data/resultados/curacion/tablas_pendientes.json` para extracción asistida o manual. De ahí
  saldrá la mayor parte de los datos por variante.
- Solo extrae Tm / dTm. No extrae Km, kcat ni actividad.
- Recall desconocido: no se ha medido contra un conjunto etiquetado a mano.
