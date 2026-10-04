"""
IDA* (Iterative Deepening A*) generico.

Motor de busqueda extraido de puzzle/puzzle_solver.py. Aqui se usa para
seleccionar variables de un dataset (ver seleccion_variables.py).

  - Funcion de evaluacion:  f(n) = g(n) + h(n)
  - Criterio TABU:          no se aplica la accion inversa de la anterior
                            (opcional, via ProblemaBusqueda.accion_inversa).
  - No expandir REPETIDOS:  conjunto 'en_camino' (estados del camino actual).
  - MEMORIA entre iteraciones: estado -> mejor g visto; poda reencuentros
                            por un camino mas caro.
  - CONTROL DE PROFUNDIDAD: 'limite' (cota de f por iteracion).

Si la heuristica es admisible (nunca sobreestima), la solucion es optima.

Uso minimo:
    class MiProblema(ProblemaBusqueda):
        def estado_inicial(self): ...
        def es_meta(self, estado): ...
        def sucesores(self, estado): yield accion, nuevo_estado, costo
        def heuristica(self, estado): ...

    res = ida_estrella(MiProblema())
    if res: print(res.acciones, res.costo)
"""
import math
import sys
from dataclasses import dataclass, field
from typing import Hashable, Iterable, List, Optional, Tuple


class ProblemaBusqueda:
    """Interfaz que debe implementar cualquier problema a resolver con IDA*.

    Los estados deben ser hashables (tuplas, strings, frozensets, ...).
    """

    def estado_inicial(self) -> Hashable:
        raise NotImplementedError

    def es_meta(self, estado) -> bool:
        raise NotImplementedError

    def sucesores(self, estado) -> Iterable[Tuple[object, Hashable, float]]:
        """Itera tuplas (accion, nuevo_estado, costo_de_la_accion)."""
        raise NotImplementedError

    def heuristica(self, estado) -> float:
        """Estimacion admisible del costo restante. 0 equivale a busqueda ciega."""
        return 0

    def accion_inversa(self, accion):
        """Accion que deshace 'accion' (criterio TABU). None = sin criterio."""
        return None


@dataclass
class ResultadoBusqueda:
    acciones: List[object]
    costo: float
    nodos_expandidos: int
    iteraciones: int
    limites: List[float] = field(default_factory=list)


_ENCONTRADO = object()


def ida_estrella(problema: ProblemaBusqueda,
                 max_iteraciones: Optional[int] = None) -> Optional[ResultadoBusqueda]:
    """Resuelve 'problema' con IDA*. Devuelve None si no hay solucion
    (o si se agota 'max_iteraciones')."""
    inicio = problema.estado_inicial()
    h0 = problema.heuristica(inicio)

    memoria = {inicio: 0}       # estado -> mejor g (persiste entre iteraciones)
    en_camino = {inicio}        # estados del camino actual (evita ciclos)
    camino: List[object] = []
    costo_solucion = [0.0]
    nodos = [0]

    def buscar(estado, g, h, limite, accion_previa):
        """DFS acotado por f <= limite. Devuelve _ENCONTRADO o el menor f excedido."""
        f = g + h
        if f > limite:                          # CONTROL DE PROFUNDIDAD
            return f
        if problema.es_meta(estado):
            costo_solucion[0] = g
            return _ENCONTRADO
        nodos[0] += 1

        tabu = problema.accion_inversa(accion_previa) if accion_previa is not None else None

        # Generar sucesores y explorar primero el de menor h.
        hijos = []
        for accion, hijo, costo in problema.sucesores(estado):
            if tabu is not None and accion == tabu:     # CRITERIO TABU
                continue
            if hijo in en_camino:                       # NODO REPETIDO (ciclo)
                continue
            hijos.append((problema.heuristica(hijo), accion, hijo, costo))
        hijos.sort(key=lambda t: t[0])

        minimo = math.inf
        for h_hijo, accion, hijo, costo in hijos:
            g_hijo = g + costo
            if g_hijo > memoria.get(hijo, math.inf):    # MEMORIA: camino peor
                continue
            memoria[hijo] = g_hijo

            en_camino.add(hijo)
            camino.append(accion)
            res = buscar(hijo, g_hijo, h_hijo, limite, accion)
            if res is _ENCONTRADO:
                return res
            minimo = min(minimo, res)
            camino.pop()
            en_camino.discard(hijo)

        return minimo

    # La recursion llega a la longitud de la solucion; se deja margen.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))

    # --- Bucle externo: cada vuelta sube la cota de f ----------------------
    limite = h0
    limites = []
    iteracion = 0
    while max_iteraciones is None or iteracion < max_iteraciones:
        iteracion += 1
        limites.append(limite)
        res = buscar(inicio, 0, h0, limite, None)
        if res is _ENCONTRADO:
            return ResultadoBusqueda(list(camino), costo_solucion[0], nodos[0], iteracion, limites)
        if res == math.inf:
            return None
        limite = res                    # nueva cota = menor f que excedio la anterior
    return None
