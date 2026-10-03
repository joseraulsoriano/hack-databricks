# Visor — guía para la persona de AR/VR

## El bucle

```
gafas ──pregunta──> endpoint ──> agente ──> RAG ──> procesamiento ──> endpoint ──eventos──> gafas
                                                                                            │
                        el grafo 3D crece mientras el agente trabaja  <─────────────────────┘
```

Tu parte son los dos extremos: la pregunta sale de las gafas y el resultado vuelve a ellas.
Los eventos llegan **en streaming**, no de golpe: los 9-13 segundos que tarda el agente son
la demo, no una pantalla de carga.

## Por qué WebXR y no una app Unity

El navegador de las Quest 2 soporta WebXR. Abres una URL, pulsas "Enter VR" y estás dentro.

- Iterar es recargar la página, no compilar e instalar un APK de minutos.
- El mismo código se prueba en un portátil con ratón, sin ponerse las gafas.
- Cinco personas pueden ver lo mismo desde sus navegadores durante el desarrollo.

Lo que se pierde: hand tracking fino, passthrough avanzado y rendimiento máximo. Para un grafo
de nodos y texto no hace falta nada de eso.

## Arrancar

```bash
uv sync
uv run uvicorn ar_vr_bridge.app:app --host 0.0.0.0 --port 8000 --reload
```

Abre `http://localhost:8000` y pulsa **Explorar**. Funciona ya, con datos simulados y citas
reales del corpus.

### Llegar a las gafas (el punto que más tiempo roba)

WebXR exige **contexto seguro**: HTTPS o localhost. Una IP de red local (`http://192.168.1.x:8000`)
**no vale**: la página carga pero el botón de VR no aparece. Dos salidas:

1. **Túnel HTTPS** (lo más simple):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   Da una URL `https://…` que se abre directamente en el navegador de las Quest.

2. **Puerto inverso por USB**, si prefieres cable:
   ```bash
   adb reverse tcp:8000 tcp:8000
   ```
   Luego abres `http://localhost:8000` **dentro** de las gafas. No lo he probado en estas Quest;
   si falla, usa el túnel.

Cuando el puente se despliegue como Databricks App, la URL ya es HTTPS y esto desaparece.

## El contrato

**`ar_vr_bridge/contract.py` es la única fuente de verdad.** Si cambia algo ahí, cambia en el
visor. Eventos que llegan por `WS /ws/explore`, en este orden:

| Evento | Qué hacer |
|---|---|
| `stage` | Texto de progreso y barra (`progress` de 0 a 1) |
| `node` | Añadir un nodo al grafo |
| `edge` | Unir dos nodos ya existentes |
| `citations` | Lista de fuentes con `url`, `title`, `snippet` |
| `answer` | Titular, conclusión, justificación y `tts_text` para voz |
| `validation` | Veredicto PASS/WARN/FAIL con los checks que lo sostienen |
| `approval_request` | **Congelar el grafo** y mostrar el panel de aprobación |
| `done` | Latencia y la siguiente pregunta que investigaría el lab |

Un nodo trae: `id`, `type`, `label` (≤48 caracteres), `detail`, `layer`, `confidence` (0-1),
`agent_generated` y `citation_ids`.

Hay también `POST /api/v1/explore` que devuelve todo de una pieza, por si necesitas probar
sin WebSocket.

## Cómo se visualiza

**Capas**, no una nube desordenada. Cada `layer` es una altura, y el bucle científico se lee
de abajo arriba:

```
layer 5   ● resultado          ← rojo
layer 4   ● experimento        ← rosa
layer 3   ● hipótesis          ← violeta, borde discontinuo (lo generó un agente)
layer 2   ● ● ● variables      ← ámbar
layer 1   ● ● ● evidencia      ← verde, una por fuente citada
layer 0   ● pregunta           ← azul, a la altura de los ojos del usuario
```

Reglas de codificación, ya implementadas en `static/index.html`:

- **Tamaño y opacidad** del nodo = `confidence`. Lo incierto se ve pequeño y tenue.
- **Borde discontinuo** = `agent_generated`. El brief exige distinguir lo que propuso un agente
  de lo que está publicado.
- **Grosor de la arista** = `weight`. Rojo si `relation` es `contradicts`.
- **Aparición animada**: cada nodo crece desde cero en 400 ms. Eso es lo que convierte la espera
  en espectáculo.
- **Giro lento del grafo** (0.0012 rad/frame): ayuda a leer la profundidad sin moverse.

Al enfocar un nodo debería verse su `detail` y sus citas. Eso está por hacer: es tu primer
trabajo real sobre el esqueleto.

## Presupuesto para Quest 2

El chip es móvil. Lo que importa:

- **90 Hz objetivo, 72 Hz aceptable.** Por debajo se nota.
- **Máximo ~120 nodos** (`MAX_NODES` en el cliente). El lab genera entre 10 y 30 por consulta.
- **Geometría y material compartidos** por tipo de nodo: ya está hecho, son 6 materiales para
  todos los nodos.
- **Etiquetas como sprites de canvas**, no geometría de texto. Un `CanvasTexture` de 512×128 por
  etiqueta; si pasas de 60 nodos, baja a 256×64.
- **Sin sombras, sin postproceso.** Ambas cosas hunden el rendimiento en Quest 2.
- `setPixelRatio(min(devicePixelRatio, 2))` ya está puesto.

## Entrada de voz

El visor hoy usa un campo de texto, que en las gafas abre el teclado virtual. Funciona pero es
lento para una demo.

La `Web Speech API` (`SpeechRecognition`) **no la he verificado en el navegador de las Quest**.
Compruébalo pronto, porque condiciona el guion de la demo. Si no funciona, dos alternativas:

1. **Panel de 3-4 preguntas preparadas**, seleccionables con el mando. Para una demo de 2 minutos
   es más fiable y se ve igual de bien.
2. **Grabar audio** con `MediaRecorder` y mandarlo al endpoint para transcribir allí.

Mi recomendación: empieza por el panel de preguntas. Si sobra tiempo, añade voz.

## Orden de trabajo

1. Arranca el puente y abre el visor en el portátil. Confirma que el grafo se dibuja. **(30 min)**
2. Consigue que se vea en las gafas con el túnel HTTPS. **(1 h — hazlo pronto, es lo que más
   sorpresas da)**
3. Panel de preguntas preparadas con el mando, en lugar del teclado. **(1 h)**
4. Enfocar un nodo muestra su detalle y sus citas. **(1-2 h)**
5. Panel de aprobación para `approval_request`. **(1 h — es el 10% de responsabilidad del brief)**
6. Si sobra: voz, o el Physarum dibujando los handoffs entre agentes.

## Estado verificado

Probado hoy: el servidor arranca, `/health` responde, el WebSocket emite los 30 eventos en orden
(10 nodos en 6 capas, 11 aristas), `POST /api/v1/explore` devuelve la respuesta completa, el panel
muestra veredicto y citas, y las citas salen de verdad de `documents_staging`.

**No verificado:** el render 3D. El navegador sin GPU que usé no crea contexto WebGL. Lo primero
que debes hacer es abrirlo en un navegador normal y confirmar que el grafo aparece.
