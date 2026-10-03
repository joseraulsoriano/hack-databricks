# Prompt de arranque — persona de orquestación con Omnigent

Esta parte vale el **30% de la nota**, más que ninguna otra. El jurado tiene que ver agentes
especialistas que se pasan resultados, usan herramientas y **cambian su plan tras un resultado**.

Pega el bloque en Claude Code desde la raíz del repo, en la rama `main`.

---

## Lo que tienes que tener en cuenta antes de escribir una línea

### 1. Omnigent es YAML, no un SDK de Python

El borrador inicial del equipo suponía un SDK (`OmnigentSession`, `Agent`, `Tool`). **No es así.**
Omnigent es un meta-harness: defines agentes en un archivo YAML y los ejecutas con `omnigent run`.

```yaml
name: lab_director
prompt: file://prompts/director.md
executor: { harness: claude-sdk }
tools:
  literature_agent:              # un subagente ES una herramienta: así se hacen los handoffs
    type: agent
    prompt: file://prompts/literature.md
    tools:
      thesis_rag: { type: mcp, url: "https://<host>/api/2.0/mcp/ai-search/workspace/lab/<index>" }
      search_staging: { type: function, callable: agent_lab.tools.search_staging }
  record: { type: function, callable: agent_lab.record.append }
policies:
  budget:
    type: function
    handler: omnigent.policies.builtins.cost.cost_budget
    factory_params: { max_cost_usd: 5.0, ask_thresholds_usd: [3.0] }
```

Tres tipos de herramienta: `function` (un callable de Python), `mcp` (una URL) y `agent`
(subagente con su propio prompt y herramientas).

### 2. El servidor gestionado ya está activo y verificado

```
https://dbc-19f58290-50fb.cloud.databricks.com/omnigent
```

`omnigent login` funciona, `/api/2.0/omnigent/health` responde `ok`. Ya está puesto como
servidor por defecto en `~/.omnigent/config.yaml`.

**Topología recomendada: runner local + servidor gestionado.**

```bash
omnigent run agent_lab/lab.yaml --server https://dbc-19f58290-50fb.cloud.databricks.com/omnigent
```

Los agentes y las herramientas corren en tu portátil (con tu suscripción de Claude); el estado
de la sesión vive en Databricks, donde el jurado y el equipo lo ven en vivo con el botón *Share*.
Eso **es** la evidencia del 30%: no hay que convencer a nadie de que los agentes colaboran, se ve.

Dos avisos:
- El workspace **no tiene modelos de Claude**. Si quieres que todo pase por Databricks, usa
  `databricks-gpt-oss-120b` o `databricks-meta-llama-3-3-70b-instruct`. Con runner local usas Claude.
- El Omnigent gestionado solo admite **políticas en CEL**, no en Python. Las comprobaciones del
  Safety Agent van como herramientas de Python; la puerta de aprobación, como política CEL.

### 3. El bucle que exige el brief

```
Pregunta → Evidencia → Hipótesis → Experimento → Resultado → Decisión actualizada
```

Tres requisitos literales que se te pueden olvidar y cuestan nota:

- **Diseña al menos dos tests posibles y elige uno** por aprendizaje esperado, viabilidad y coste.
  Que la elección quede escrita, con su razón.
- **El planner tiene presupuesto.** Por eso la política `cost_budget` no es decorativa.
- **Un resultado sorprendente reabre una suposición anterior.** Esto es lo que separa un pipeline
  de un laboratorio. Si el experimento contradice la hipótesis, el Analysis Agent debe volver
  atrás y proponer otra, y eso tiene que verse en el registro.

### 4. Los agentes, y qué decisión posee cada uno

| Agente | Decisión que posee | Herramientas |
|---|---|---|
| `lab_director` | Qué test correr con el presupuesto disponible | los subagentes |
| `literature_agent` | Qué evidencia es relevante y citable | RAG, `search_staging`, `fetch_sources` |
| `insight_agent` | Qué hipótesis es comprobable en 24 h | — |
| `experiment_runner` | Cómo se ejecuta el test elegido | `run_matched_experiment`, `ga_search` |
| `analysis_agent` | Qué significa el resultado y qué sigue | — |
| `safety_agent` | Si la afirmación puede salir al visor | `verify_citations`, `robustness_checks` |

### 5. Lo que ya existe

| Archivo | Qué es |
|---|---|
| `agent_lab/tools.py` | `fetch_sources` (el agente amplía el corpus), `search_staging`, `staging_stats` |
| `ar_vr_bridge/contract.py` | El formato que espera el visor |
| `ar_vr_bridge/app.py` | Espera un `agent_lab.runtime.stream_discovery(query, query_id)`; si no existe, cae al simulador |
| `workspace.lab.research_record` | Tabla del registro compartido, creada y vacía |
| `workspace.lab.pet_activity_ml` | 1 570 filas × 42 columnas, lista para el experimento |

**Tu punto de conexión con el visor es una sola función:** `agent_lab/runtime.py` con
`stream_discovery` como generador asíncrono que emite los eventos de `contract.py`. En cuanto
exista, el visor deja de usar el simulador sin cambiar nada más.

### 6. La latencia no es un detalle

La interacción es por voz (ElevenLabs). La voz debe empezar a hablar **antes de 1,5 s**.
El bucle completo tarda 9-13 s, así que no puede estar en el camino crítico.

Emite `stage` y los primeros nodos de evidencia **en cuanto el RAG responda**, sin esperar al
experimento. El visor y la voz consumen lo que llega; lo lento sigue llegando de fondo.

---

## El prompt

```
Trabajo en el reto "Agentic Scientific Discovery" (Databricks x Hack-Nation, 24 h).
Mi parte es la orquestacion con Omnigent, que vale el 30% de la nota.
Lee docs/PROMPT_AGENTE.md, ar_vr_bridge/contract.py y agent_lab/tools.py antes de
tocar nada.

CONTEXTO
- Nicho: enzimas que degradan PET. Pregunta: que propiedades de una PET hidrolasa
  predicen su actividad a 60 C, y puede un lab de agentes encontrarlas con menos
  evaluaciones que un cribado exhaustivo.
- Omnigent gestionado activo y verificado en
  https://dbc-19f58290-50fb.cloud.databricks.com/omnigent
- Datos listos en workspace.lab (perfil CLI hack):
  documents_staging 4945 docs, pet_activity_ml 1570 filas x 42 columnas,
  research_record vacia, mutant_stability vacia.
- El workspace NO tiene modelos de Claude. Usa el harness claude-sdk en local, o
  databricks-gpt-oss-120b si quieres que todo pase por Databricks.

TAREAS, en este orden

1. agent_lab/lab.yaml con seis agentes, cada uno con una decision que posee:
   lab_director (elige test con presupuesto), literature_agent (evidencia citable),
   insight_agent (hipotesis comprobable), experiment_runner (ejecuta),
   analysis_agent (interpreta y decide el siguiente paso), safety_agent (autoriza
   o bloquea la salida). Los handoffs se hacen con subagentes: type: agent.
   Politicas: cost_budget con max_cost_usd 5, y aprobacion humana antes de
   escribir en documents_curated o mutant_stability.

2. Las herramientas que faltan en agent_lab/:
   - run_matched_experiment: baseline vs propuesto sobre pet_activity_ml, mismos
     folds AGRUPADOS POR enzyme_id (un split aleatorio filtra informacion porque
     la misma enzima aparece en varias condiciones), early stopping, macro-F1
     (el dataset esta desbalanceado 29/71), media +/- desviacion y gap train/val.
   - verify_citations: comprueba que cada afirmacion apunta a un doc_id recuperado.
   - robustness_checks: los umbrales del safety agent.
   - record.append: escribe cada handoff y decision en workspace.lab.research_record.

3. El bucle completo. Requisitos del brief que NO se pueden saltar:
   - el planner disena AL MENOS DOS tests y elige uno justificando por aprendizaje
     esperado, viabilidad y coste
   - un resultado que contradiga la hipotesis REABRE la suposicion anterior y
     genera otra; esto tiene que quedar registrado
   - toda afirmacion lleva cita; las hipotesis van etiquetadas como generadas por agente

4. agent_lab/runtime.py con stream_discovery(query, query_id) como generador
   asincrono que emite los eventos de ar_vr_bridge/contract.py. Emite stage y los
   nodos de evidencia EN CUANTO el RAG responda, sin esperar al experimento: la voz
   debe empezar a hablar antes de 1.5 s.

5. Mide la aceleracion: cuanto tarda el lab en extraer N datos de la literatura
   frente a hacerlo a mano. Reporta el multiplicador REAL que observes, aunque sea
   1.5x. El brief dice que la fuerza de la evidencia importa mas que el numero.

REGLAS
- Omnigent es YAML, no un SDK de Python. omnigent run agent_lab/lab.yaml
- Nada se escribe en documents_curated ni mutant_stability sin aprobacion humana.
- ar_vr_bridge/contract.py es la fuente de verdad del formato de salida.
- No pongas atribucion a herramientas de IA en los commits.

Empieza por el paso 1 y ensename el lab.yaml antes de implementar las herramientas.
```

---

## Qué hay que entregar (lo pide el brief)

Repositorio · especificación de agentes y políticas · demo de 2 minutos · evidencia citada ·
código y resultados del experimento · **la mejora medida** · el siguiente experimento.

Lo que más se olvida es **la mejora medida**. Mídela desde el principio: cronometra cuánto
tarda una persona en extraer 10 datos de la literatura a mano, y compáralo con el agente.
Un 3× honesto y demostrable puntúa más que un 10× sin respaldo.
