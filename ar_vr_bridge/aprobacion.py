"""Puerta de aprobación humana. **La decisión vuelve al orquestador, no a la interfaz.**

Esto es lo que el brief llama *human approval*, y el matiz importa: el visor no
decide nada por su cuenta. El orquestador pide la aprobación, **se queda esperando**,
y la respuesta de la persona le llega a él para que cambie su plan. Si la decisión
muriera en la interfaz, el bucle no se cerraría y la revisión humana sería decorativa.

    orquestador                visor / gafas                persona
        │  emite ApprovalEvent ───►  congela el grafo  ──►  aprueba o rechaza
        │                                                        │
        │  ◄──────────── pedir() devuelve la decisión ───────────┘
        │
        └─► cambia el plan: reabre una suposición, descarta el test, sigue

Vive fuera de `app.py` a propósito: `agent_lab/runtime.py` tiene que poder importarlo
sin provocar un ciclo (es `app` quien importa `runtime`, no al revés).

    from ar_vr_bridge import aprobacion

    decision = await aprobacion.pedir(approval_id)   # 'approve' | 'reject' | 'timeout'
"""

import asyncio

# Un futuro por aprobación en vuelo. En memoria: una aprobación no sobrevive a un
# reinicio, y es correcto — al reiniciar nadie está mirando el panel.
_pendientes: dict[str, asyncio.Future] = {}

PLAZO_POR_DEFECTO = 120.0


def pendientes() -> list[str]:
    return sorted(_pendientes)


async def pedir(approval_id: str, timeout_s: float = PLAZO_POR_DEFECTO) -> str:
    """Bloquea hasta que una persona responda. Devuelve 'approve', 'reject' o 'timeout'.

    Al vencer el plazo devuelve `timeout` y **no** se da por aprobado: quien llama
    decide qué hacer, pero el silencio nunca cuenta como un sí.
    """
    futuro = asyncio.get_running_loop().create_future()
    _pendientes[approval_id] = futuro
    try:
        return await asyncio.wait_for(futuro, timeout_s)
    except TimeoutError:
        return "timeout"
    finally:
        _pendientes.pop(approval_id, None)


def responder(approval_id: str, decision: str) -> bool:
    """Entrega la decisión de la persona a quien esté esperando.

    Devuelve False si nadie espera por ese identificador: o no existe, o ya venció,
    o ya se respondió. El visor lo muestra como `unknown_approval` en vez de fingir
    que se registró.
    """
    futuro = _pendientes.get(approval_id)
    if futuro is None or futuro.done():
        return False
    futuro.set_result(decision if decision in ("approve", "reject") else "reject")
    return True
