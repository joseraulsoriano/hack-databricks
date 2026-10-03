# Alcance, límites y responsabilidad

Declaración explícita de qué hace y qué **no** hace este proyecto. Responde al criterio de
*creatividad y responsabilidad* del reto (10%) y al requisito del brief de documentar controles,
puertas de aprobación humana y la validación que faltaría antes de cualquier uso real.

---

## Qué es este proyecto

Un sistema de **recuperación de información y análisis estadístico** sobre literatura científica
publicada y datos experimentales ya publicados, en el dominio de las enzimas que degradan PET
(tereftalato de polietileno, el plástico de las botellas).

Concretamente, el sistema:

1. **Descarga literatura y metadatos** de APIs públicas y abiertas: Europe PMC, OpenAlex,
   RCSB PDB, AlphaFold DB y Zenodo.
2. **Organiza y deduplica** ese corpus en tablas, con la licencia y la cita de cada documento.
3. **Indexa el texto** para búsqueda semántica, de modo que un agente pueda responder preguntas
   citando el artículo y la página de origen.
4. **Calcula descriptores fisicoquímicos agregados** a partir de secuencias ya publicadas
   (peso molecular, punto isoeléctrico, carga, hidrofobicidad, composición de aminoácidos),
   usando la biblioteca estándar Biopython.
5. **Entrena modelos estadísticos** que relacionan esos descriptores con la actividad medida
   experimentalmente y publicada por terceros, para estimar qué condiciones de ensayo
   (pH, temperatura, tipo de sustrato) se asocian a mayor actividad.

El resultado es un **mapa de la literatura existente** y un modelo que indica qué publicaciones
y qué condiciones de ensayo merece la pena revisar primero.

---

## Qué NO hace este proyecto

Esto es una lista cerrada y deliberada:

- **No diseña ni genera secuencias de proteínas.** No hay modelo generativo de secuencias,
  ni diseño de novo, ni optimización de secuencias. Las 213 secuencias del dataset son las
  ya publicadas por sus autores; el código las lee, no las modifica ni propone variantes nuevas.
- **No propone modificaciones genéticas** ni rutas de ingeniería de organismos.
- **No contiene protocolos de laboratorio** húmedo: ni expresión, ni purificación, ni clonación,
  ni manipulación de organismos. Las condiciones experimentales que aparecen en los datos
  (pH, temperatura, sustrato) son metadatos de ensayos ya realizados y publicados por terceros.
- **No trabaja con agentes patógenos, toxinas ni organismos de riesgo.** Las PET hidrolasas son
  enzimas industriales que hidrolizan plástico. No tienen actividad sobre tejido vivo y se
  estudian abiertamente para reciclaje desde 2016.
- **No produce datos experimentales nuevos.** Todo valor numérico procede de un dataset
  publicado con licencia CC-BY-4.0 y DOI verificable.
- **No sustituye el ensayo de laboratorio.** Los modelos producen estimaciones con su margen
  de error; sirven para priorizar lectura, no para concluir nada sobre el mundo físico.

---

## Contexto del dominio

Las PET hidrolasas son el caso de estudio porque son un problema **ambiental** bien delimitado
y con literatura abundante y abierta. Degradan plástico PET en sus componentes solubles,
lo que permite reciclarlo. Es un área de investigación pública desde el descubrimiento de
*Ideonella sakaiensis* en 2016, con cientos de artículos de acceso abierto, estructuras en
dominio público (CC0) y datasets publicados bajo CC-BY.

El cuello de botella que ataca el proyecto es **documental, no biológico**: no existe una
tabla consolidada que reúna lo que cientos de artículos ya publicaron por separado. Reunir
esa información a mano lleva días. Ese es el tiempo que el sistema busca reducir.

---

## Controles implementados

| Control | Cómo está implementado |
|---|---|
| **Aprobación humana obligatoria** | Los agentes escriben en `documents_staging` con `fetched_by='agent:<nombre>'`. Nada llega al índice sin que una persona lo pase a `documents_curated`. |
| **Cita obligatoria** | Toda afirmación requiere `doc_id`; en `mutant_stability`, además `evidence_span` con la frase exacta de origen. |
| **Trazabilidad completa** | `research_record` registra cada handoff, decisión y aprobación. Toda conclusión se puede reconstruir hasta su fuente. |
| **Hipótesis etiquetadas** | Lo generado por un agente se marca como tal y nunca se presenta como hecho establecido. |
| **Licencias preservadas** | `license` por documento; no todo el acceso abierto es CC-BY y el informe lo declara. |
| **Incertidumbre explícita** | Las métricas se reportan como media ± desviación en validación cruzada, con el gap train/val. Sin intervalo, no hay afirmación. |
| **Presupuesto del agente** | Política de coste en Omnigent; el planner elige entre tests compitiendo por un presupuesto limitado. |

---

## Validación que faltaría antes de cualquier uso real

Declarado de forma explícita, como pide el brief:

1. Los modelos se entrenan con **213 enzimas y 1 570 mediciones**. Es un dataset pequeño:
   las estimaciones son orientativas y no extrapolables fuera de su rango de condiciones.
2. El dataset está **desbalanceado (29% con actividad > 0)**; por eso se reporta macro-F1 y
   nunca accuracy.
3. Los descriptores son **agregados de secuencia**, no estructura ni dinámica. Ignoran el sitio
   activo y el plegamiento.
4. **Nada de lo que produce este sistema se ha verificado experimentalmente.** El siguiente paso
   real sería que un laboratorio con los permisos y la infraestructura correspondientes repitiera
   los ensayos ya publicados. Eso queda fuera del alcance del proyecto y de un hackathon.

---

## Fuentes y licencias

| Fuente | Licencia |
|---|---|
| Europe PMC | Por artículo; se conserva en `license` |
| OpenAlex | CC0 |
| RCSB PDB | CC0 |
| AlphaFold DB | CC-BY-4.0 |
| Zenodo [10.5281/zenodo.15417757](https://doi.org/10.5281/zenodo.15417757) — Norton-Baker et al., 2025 | CC-BY-4.0 |

Todas son bases de datos públicas, abiertas y de uso habitual en investigación académica.
