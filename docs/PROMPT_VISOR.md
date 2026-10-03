# Prompt de arranque — persona de visor e interacción por voz

Pega el bloque en Claude Code desde la raíz del repo, en la rama `test`.

---

## Lo que tienes que tener en cuenta antes de escribir una línea

### 1. La latencia manda sobre todo lo demás

Con voz de ElevenLabs, el sistema es una **conversación**. Una conversación se rompe si el
interlocutor tarda más de **dos segundos** en empezar a hablar. Hoy el bucle completo del
laboratorio tarda entre 9 y 13 segundos. Si esperas a que termine para hablar, la demo está
muerta aunque todo funcione.

La regla es: **hablar primero, calcular después.**

```
t=0.0s  usuario termina de hablar
t=0.4s  transcripción lista
t=0.8s  ← la voz YA está diciendo algo real: "Tengo tres estudios sobre eso..."
t=1.5s  el RAG devolvió sus fragmentos; la voz da la respuesta corta con su cita
t=3-12s  el experimento y la validación llegan de fondo y entran en el grafo 3D
         sin interrumpir a la voz
```

El usuario percibe 0,8 segundos. Los 12 reales ocurren mientras ya está escuchando y viendo
crecer el grafo.

### 2. Separa el camino rápido del lento

No pongas cinco agentes en serie antes de la primera palabra. Esa es la causa número uno de
latencia en estos sistemas.

| Camino | Qué corre | Presupuesto | Quién lo consume |
|---|---|---|---|
| **Rápido** | Búsqueda en el RAG + respuesta corta con cita | < 1,5 s | La voz |
| **Lento** | Hipótesis, experimento, validación | 5-15 s | El grafo 3D, en streaming |

Un solo agente de literatura contesta por voz. El resto trabaja de fondo y va poblando el
grafo. Cuando el resultado llega, la voz puede añadir una frase: "El experimento confirma que…".

### 3. Streaming en los dos extremos

- **Del LLM a ElevenLabs:** no esperes la respuesta completa. ElevenLabs acepta texto en
  streaming; manda los tokens según salen y el audio empieza antes de que el modelo termine.
- **Del puente al visor:** ya está hecho. `WS /ws/explore` emite eventos uno a uno
  (`stage`, `node`, `edge`, `answer`, `validation`) en vez de un JSON al final.

### 4. Los arranques en frío te van a morder

Tres cosas tienen cold start y las tres se notan en la primera pregunta, que suele ser la de
la demo:

- El SQL warehouse de Databricks (parado por defecto, tarda en arrancar).
- El endpoint de Vector Search.
- El endpoint del modelo.

Lánzales una consulta de calentamiento al empezar la sesión y repítela cada pocos minutos.

### 5. Qué ya existe y no tienes que construir

| Archivo | Qué es |
|---|---|
| `ar_vr_bridge/contract.py` | **Única fuente de verdad** de lo que viaja al visor |
| `ar_vr_bridge/app.py` | `WS /ws/explore`, `POST /api/v1/explore`, puerta de aprobación |
| `ar_vr_bridge/mock.py` | Simula el bucle con citas reales del corpus, sin agentes |
| `ar_vr_bridge/static/index.html` | Esqueleto Three.js + WebXR, probado salvo el render 3D |
| `docs/VISOR.md` | Contrato de eventos, presupuesto de rendimiento, capas del grafo |

Trátalo como punto de partida, no como algo intocable. Si el diseño no te sirve, cámbialo.

### 6. Dos cosas que te van a robar horas si no las atacas primero

- **WebXR exige contexto seguro.** `http://192.168.1.x:8000` carga la página pero **no muestra
  el botón de VR**. Necesitas HTTPS: `cloudflared tunnel --url http://localhost:8000`.
  Resuélvelo en la primera hora.
- **No he verificado la API de voz del navegador de las Quest.** Si usas ElevenLabs para
  entrada además de salida, comprueba cuanto antes que puedes capturar micrófono desde ese
  navegador. Si no se puede, el plan B es un panel de 3-4 preguntas preparadas con el mando:
  para una demo de dos minutos es más fiable.

---

## El prompt

```
Trabajo en el reto "Agentic Scientific Discovery" (Databricks x Hack-Nation, 24 h).
Mi parte: el visor en Meta Quest 2 y la interaccion por voz con ElevenLabs.
Lee docs/VISOR.md y ar_vr_bridge/contract.py antes de tocar nada.

CONTEXTO
- Nicho: enzimas que degradan PET. El lab responde preguntas cientificas citando
  literatura publicada y muestra el razonamiento como un grafo 3D que crece.
- Ya existe y funciona: el puente FastAPI (ar_vr_bridge/) con WebSocket en streaming,
  un simulador que emite el bucle completo con citas reales del corpus, y un
  esqueleto de cliente WebXR en Three.js.
- Arranca con: uv run uvicorn ar_vr_bridge.app:app --host 0.0.0.0 --port 8000 --reload
  y abre http://localhost:8000

RESTRICCION PRINCIPAL: LATENCIA
La voz debe empezar a hablar en menos de 1.5 segundos desde que el usuario
termina su pregunta. El bucle cientifico completo tarda 9-13 s, asi que NO
puede estar en el camino critico de la voz.

Separa dos caminos:
- Rapido (<1.5s): busqueda en el RAG + respuesta corta con cita -> va a la voz
- Lento (5-15s): hipotesis, experimento, validacion -> va al grafo 3D en streaming

Manda los tokens del LLM a ElevenLabs segun salen, sin esperar la respuesta completa.

TAREAS, en este orden
1. Abre el visor en un navegador con GPU y confirma que el grafo 3D se dibuja.
   No esta verificado: el navegador sin GPU que uso mi companero no pudo renderizarlo.
2. Consigue verlo dentro de las Quest 2 con un tunel HTTPS. Hazlo pronto.
3. Integra ElevenLabs: salida de voz primero (que hable el resultado), despues
   entrada si el navegador de las Quest lo permite.
4. Implementa el camino rapido: un endpoint que solo consulte el RAG y devuelva
   una respuesta corta con cita, por debajo de 1.5 s. Mide la latencia real y
   reportala.
5. Enfocar un nodo muestra su detalle y sus citas.
6. Panel de aprobacion humana para el evento approval_request: el grafo se congela
   hasta que una persona responde. Esto puntua en el criterio de responsabilidad.

REGLAS
- ar_vr_bridge/contract.py es la fuente de verdad. Si cambias un campo, cambialo ahi
  y avisa al equipo.
- Mide latencias reales y registralas; no estimes.
- Toda respuesta por voz debe poder decir de donde salio el dato.
- Trabaja en la rama test y haz commits pequenos.
- No pongas atribucion a herramientas de IA en los commits.

Empieza por el paso 1 y dime que ves antes de seguir.
```

---

## Guion de la demo (2 minutos)

El brief pide una demo de dos minutos. Construye hacia esto:

1. Alguien se pone las gafas y pregunta en voz alta.
2. **A los 0,8 s la voz ya responde** y el nodo de la pregunta aparece flotando.
3. Mientras habla, van surgiendo los nodos de evidencia, uno por fuente citada.
4. La hipótesis aparece con borde discontinuo: la propuso un agente, no está publicada.
5. El experimento corre y el resultado baja con su veredicto.
6. El Safety Agent marca el gap de sobreajuste y **pide aprobación humana**. El grafo se
   congela hasta que la persona decide.
7. La voz cierra diciendo qué investigaría a continuación.

Lo que el jurado tiene que ver sin que nadie se lo explique: **los agentes colaboran** (30% de
la nota) y **el sistema se niega a afirmar lo que no puede sostener** (10%).
