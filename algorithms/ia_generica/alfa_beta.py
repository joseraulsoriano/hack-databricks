"""
Poda Alfa-Beta generica (version fail-hard, segun pseudocodigo de clase).

Motor extraido de Ghost.py (Pac-Man). Aqui se usa para elegir el modelo mas
robusto de un dataset: modelo (MAX) contra escenario de datos adverso (MIN).
Ver seleccion_robusta.py.

  Nodo terminal (profundidad 0 o es_terminal): devuelve evaluar(estado).
  Nodo MAX:  alpha <- max(alpha, hijo);  si alpha >= beta  -> devolver beta.
  Nodo MIN:  beta  <- min(beta,  hijo);  si alpha >= beta  -> devolver alpha.
  Primer llamado: alpha = -inf, beta = +inf.

Uso minimo:
    class MiJuego(Juego):
        def acciones(self, estado, turno_max): ...
        def resultado(self, estado, accion, turno_max): ...
        def es_terminal(self, estado): ...
        def evaluar(self, estado): ...     # mayor = mejor para MAX

    accion, valor = mejor_accion(MiJuego(), estado, profundidad=4)
"""
import math
from typing import Hashable, List, Tuple


class Juego:
    """Interfaz que debe implementar cualquier juego a resolver con alfa-beta."""

    def acciones(self, estado, turno_max: bool) -> List[object]:
        """Acciones legales del jugador en turno (MAX si turno_max, si no MIN)."""
        raise NotImplementedError

    def resultado(self, estado, accion, turno_max: bool) -> Hashable:
        """Estado que resulta de aplicar 'accion' del jugador en turno."""
        raise NotImplementedError

    def es_terminal(self, estado) -> bool:
        return False

    def evaluar(self, estado) -> float:
        """Funcion de evaluacion desde el punto de vista de MAX."""
        raise NotImplementedError


def alfa_beta(juego: Juego, estado, profundidad: int,
              alpha: float = -math.inf, beta: float = math.inf,
              turno_max: bool = True) -> float:
    """Valor minimax de 'estado' con poda alfa-beta (fail-hard)."""
    if profundidad == 0 or juego.es_terminal(estado):
        return juego.evaluar(estado)

    acciones = juego.acciones(estado, turno_max)
    if not acciones:                    # sin jugadas: se evalua como hoja
        return juego.evaluar(estado)

    if turno_max:
        for a in acciones:
            hijo = juego.resultado(estado, a, True)
            alpha = max(alpha, alfa_beta(juego, hijo, profundidad - 1, alpha, beta, False))
            if alpha >= beta:           # PODA BETA
                return beta
        return alpha

    for a in acciones:
        hijo = juego.resultado(estado, a, False)
        beta = min(beta, alfa_beta(juego, hijo, profundidad - 1, alpha, beta, True))
        if alpha >= beta:               # PODA ALFA
            return alpha
    return beta


def mejor_accion(juego: Juego, estado, profundidad: int,
                 turno_max: bool = True) -> Tuple[object, float]:
    """Elige la accion del jugador en turno. Devuelve (accion, valor).

    A diferencia de Ghost.path_alfa_beta, alpha/beta se arrastran entre los
    hijos de la raiz, asi tambien se poda en el primer nivel.
    """
    acciones = juego.acciones(estado, turno_max)
    if not acciones:
        return None, juego.evaluar(estado)

    alpha, beta = -math.inf, math.inf
    mejor, mejor_val = acciones[0], (-math.inf if turno_max else math.inf)
    for a in acciones:
        hijo = juego.resultado(estado, a, turno_max)
        val = alfa_beta(juego, hijo, profundidad - 1, alpha, beta, not turno_max)
        if turno_max and val > mejor_val:
            mejor, mejor_val = a, val
            alpha = max(alpha, val)
        elif not turno_max and val < mejor_val:
            mejor, mejor_val = a, val
            beta = min(beta, val)
    return mejor, mejor_val
