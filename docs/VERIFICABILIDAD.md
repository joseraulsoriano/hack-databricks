# Verificabilidad — cómo se justifica cada afirmación

Responde a una pregunta concreta: *¿cómo demostramos que lo que sale de aquí es real, y no
una respuesta plausible?* Y a la de al lado: *¿cómo defendemos que un procesamiento de dos
minutos sirve de algo frente a meses de investigación?*

---

## 1. No todas las afirmaciones se justifican igual

Hay tres clases de afirmación en el sistema y **sólo la primera puede ser verdadera como 2+2**.
Mezclarlas es lo que hunde un proyecto en revisión.

| Clase | Ejemplo | Cómo se justifica | ¿Certeza tipo 2+2? |
|---|---|---|---|
| **Procedencia** | «el artículo `europepmc:111` contiene la frase *“…melting temperature of 85.8 °C…”*» | Cotejo de cadena contra el documento aprobado | **Sí.** Decidible, reproducible, comprobable a mano |
| **Agregación** | «hay 3 352 DOIs distintos y 1 016 repetidos en 2 222 filas» | Conteo sobre una tabla con criterio escrito | **Sí**, dado el criterio. El criterio se discute; el conteo no |
| **Modelo** | «este descriptor se asocia a mayor actividad a 60 °C» | Validación cruzada con partición publicada, contra línea base, con intervalo | **No, nunca.** Es una estimación con error |

La puerta de procedencia (`agent_lab/procedencia.py`) industrializa la primera clase. Las
cautelas de la tercera están en [`ALCANCE.md`](ALCANCE.md) y en `algorithms/`.

> **Lo que el sistema afirma:** que consolidó, con la cita exacta de cada dato, lo que cientos
> de artículos publicaron por separado.
> **Lo que no afirma:** haber descubierto nada sobre el mundo físico. Eso lo decide un
> laboratorio, no este repo.

---

## 2. La entrada son hipótesis con respaldo, no preguntas

El lab **no acepta preguntas abiertas** para luego pivotarlas. Acepta hipótesis que ya traen
la evidencia que dicen tener, y una puerta determinista decide si entran.

```json
POST /api/v1/hypothesis
{
  "statement": "Las mutaciones que rigidizan el entorno del sitio activo suben la Tm de una PET hidrolasa",
  "prediction": "Las variantes con Tm publicada por encima de 80 °C conservan actividad medida a 60 °C",
  "variables": ["temperature_c", "activity"],
  "respaldo": [
    {"doc_id": "europepmc:111",
     "evidence_span": "The engineered variant LCC-ICCG showed a melting temperature of 85.8 degrees C, an increase over the wild-type enzyme.",
     "value": 85.8, "unit": "C", "record_id": "rec_ok"}
  ],
  "submitted_by": "human:equipo"
}
```

Una hipótesis sin `respaldo` se rechaza por `respaldo_presente`: sin evidencia citada es una
pregunta, y las preguntas no entran por esta puerta. Eso es, literalmente, la diferencia entre
*entrada curada* y *prompt*.

---

## 3. Qué comprueba la puerta

Cada comprobación es binaria, tiene su valor y su umbral en el recibo, y ninguna la decide un
modelo de lenguaje.

| Comprobación | Qué exige | Fatal |
|---|---|---|
| `respaldo_presente` | Al menos una pieza de evidencia | sí |
| `enunciado_presente` / `prediccion_presente` | Afirma algo, y algo medible que lo pueda contradecir | sí |
| `variables_existen` | Las variables son columnas reales de `pet_activity_ml` | sí |
| `doc_existe` | El `doc_id` está en `documents_curated` | sí |
| `doc_aprobado` | Ese documento tiene `approved_by`: una persona lo promovió | sí |
| `span_suficiente` | El `evidence_span` mide ≥ 40 caracteres (una frase, no tres palabras) | sí |
| `span_literal` | **La frase aparece literal en ese documento** | sí |
| `valor_en_span` | El número afirmado está escrito en su propia frase | sí |
| `registro_existe` / `registro_verificado` | La fila de `mutant_stability` existe y tiene `verified = true` | sí |
| `valor_coincide_registro` | El número afirmado es el de la fila citada | sí |
| `fuentes_distintas` | Evidencia de ≥ 2 fuentes | **no**, avisa |

Veredicto: `ADMITIDA`, `ADMITIDA_CON_AVISOS` o `RECHAZADA`.

### `span_literal` es el núcleo

Es cotejo de cadena, no parecido semántico. La normalización es **declarada y mínima** —NFKC,
guiones Unicode unificados, espacios colapsados, sin distinguir mayúsculas— precisamente para
que una persona pueda abrir el PDF, leer la frase y verla. El guion no es un capricho: `LCC-ICCG`
aparece con U+2010 en unos artículos y con guion ASCII en otros.

Cuando una frase no está en el documento al que se le atribuye **pero sí en otro del corpus**, el
recibo dice en cuál. Ese es el error de atribución típico: la evidencia es real y el `doc_id` es
de otro artículo.

### Lo que la puerta no puede hacer

- **No prueba que la hipótesis sea cierta.** Admitida = fundada y trazable; sigue siendo hipótesis.
- **No valida razonamiento.** Que la evidencia exista no implica que sostenga la conclusión.
- **No cruza trozos.** Un `evidence_span` partido entre dos chunks no casa, y es correcto: el
  trozo es la unidad que una persona aprobó.
- **No sustituye al segundo revisor.** Con un solo revisor el criterio no se puede comprobar como
  repetible (ver [`EXTRACCION_MUTANTES.md`](EXTRACCION_MUTANTES.md)).

---

## 4. El recibo es reproducible

Cada respuesta lleva `receipt_hash`: SHA-256 de la hipótesis más el resultado de cada
comprobación. Mismo input sobre el mismo corpus → mismo hash. Cambiar un decimal lo cambia.

Eso es lo que hace auditable la demo: el recibo no es un sello de confianza, es un cálculo que
un tercero repite y compara. Si no coincide, algo cambió, y el `research_record` dice qué.

---

## 5. Los dos minutos, dicho con honestidad

El encuadre defendible no es «dos minutos sustituyen meses de investigación». Es:

> Reproducimos en minutos una tabla que a mano lleva días, y **cada celda apunta a la frase
> exacta de la que salió**, en un documento que una persona aprobó.

La investigación original sigue siendo de los autores de los documentos del corpus. Lo que se
comprime es el trabajo **documental** —leer, cotejar y tabular lo ya publicado—, que es el cuello
de botella que declara [`ALCANCE.md`](ALCANCE.md). Esa afirmación es verificable en vivo: se abre
cualquier número y se llega a su frase.

---

## 6. Cómo se comprueba todo esto

```bash
uv run python -m unittest discover -s tests -v    # incluye la puerta y el endpoint, sin red
```

`tests/test_procedencia.py` fija cada forma conocida de colar un dato que no se sostiene: la
frase parafraseada que suena bien, la atribuida al artículo equivocado, el fragmento corto que
casa con todo, el valor `85` dado por bueno porque el texto dice `85.8`, la fila que nadie
revisó, el documento sin `approved_by`.

Si el warehouse no responde, el endpoint devuelve `RECHAZADA` con `corpus_inaccesible`: sin poder
cotejar, no se admite a ciegas.
