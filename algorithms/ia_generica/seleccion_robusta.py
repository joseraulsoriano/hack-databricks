"""
Seleccion del modelo mas ROBUSTO para un dataset con poda alfa-beta.

Pregunta que responde: ¿que modelo rinde mejor en el PEOR caso de datos que
puede encontrar en la practica (ruido, huecos, menos filas, etiquetas mal
cargadas)? Es un juego de dos jugadores (ver alfa_beta.py):

  - MAX (analista):   elige un modelo candidato.
  - MIN (adversario): elige el escenario de datos que mas lo perjudica.
  - Hoja:             se entrena el modelo con el train del escenario y se
                      mide el score sobre el test del escenario.
  - Valor de un modelo = su score en el peor escenario (minimax).

La poda: si un escenario ya deja a un modelo por debajo del mejor peor-caso
conocido (alpha), ese modelo no puede ganar y sus escenarios restantes NO se
entrenan. Asi se ahorran entrenamientos.

Escenarios (se aplican con semilla fija):
  original           sin cambios
  ruido_test         ruido gaussiano (20% de la desv. estandar) en el test
  faltantes          20% de celdas vacias en train y test
  pocos_datos        se entrena solo con el 30% del train
  etiquetas_ruidosas 10% de las etiquetas del train alteradas

Uso:
    python3 seleccion_robusta.py datos.csv --objetivo Class
    python3 seleccion_robusta.py datos.csv --objetivo precio --modelos arbol bosque lineal
    python3 seleccion_robusta.py datos.csv --objetivo Class \\
        --mlp-config resultados_mlp/mejor_individuo.json      # incluye la red del genetico
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from joblib import dump
from sklearn.metrics import get_scorer

import datos
from alfa_beta import Juego, mejor_accion

ESCENARIOS = ["original", "ruido_test", "faltantes", "pocos_datos", "etiquetas_ruidosas"]


def aplicar_escenario(nombre, X_train, y_train, X_test, y_test, tarea, semilla):
    """Devuelve (X_train, y_train, X_test, y_test) alterados segun el escenario."""
    rng = np.random.default_rng(semilla)
    X_train, X_test = X_train.copy(), X_test.copy()
    # float: el ruido de 'etiquetas_ruidosas' tiene decimales y pandas 3 no lo
    # admite en una columna de enteros.
    y_train = y_train.astype(float) if tarea == datos.REGRESION else y_train.copy()
    numericas = X_train.select_dtypes(include="number").columns

    if nombre == "ruido_test":
        std = X_train[numericas].std()
        X_test[numericas] = X_test[numericas] + rng.normal(0, 1, X_test[numericas].shape) * (0.2 * std.values)
    elif nombre == "faltantes":
        X_train = X_train.mask(rng.random(X_train.shape) < 0.20)
        X_test = X_test.mask(rng.random(X_test.shape) < 0.20)
    elif nombre == "pocos_datos":
        n = min(len(X_train), max(2, int(0.3 * len(X_train))))
        idx = rng.choice(len(X_train), n, replace=False)
        X_train, y_train = X_train.iloc[idx], y_train.iloc[idx]
        if tarea == datos.CLASIFICACION and y_train.nunique() < 2:
            raise ValueError("'pocos_datos' dejo una sola clase en el train: usa menos "
                             "escenarios o un dataset mas grande.")
    elif nombre == "etiquetas_ruidosas":
        idx = rng.choice(len(y_train), int(0.10 * len(y_train)), replace=False)
        if tarea == datos.CLASIFICACION:
            # La etiqueta nueva es SIEMPRE distinta de la original, para que el
            # ruido sea del 10% de verdad y no de la mitad.
            clases = y_train.unique()
            y_train.iloc[idx] = [rng.choice([c for c in clases if c != v]) if len(clases) > 1 else v
                                 for v in y_train.iloc[idx]]
        else:
            y_train.iloc[idx] = y_train.iloc[idx] + rng.normal(0, y_train.std(), len(idx))
    elif nombre != "original":
        raise ValueError(f"Escenario desconocido '{nombre}'. Opciones: {ESCENARIOS}")
    return X_train, y_train, X_test, y_test


class JuegoRobustez(Juego):
    """Estado = tupla de decisiones: () -> (modelo,) -> (modelo, escenario)."""

    def __init__(self, candidatos, escenarios, X_train, y_train, X_test, y_test,
                 tarea, metrica, semilla=42):
        self.candidatos = candidatos        # nombre -> funcion que crea el modelo
        self.escenarios = escenarios
        self.datos = (X_train, y_train, X_test, y_test)
        self.tarea, self.semilla = tarea, semilla
        self.scorer = get_scorer(metrica)
        self.resultados = {}                # (modelo, escenario) -> score (hojas entrenadas)

    def acciones(self, estado, turno_max):
        return list(self.candidatos) if len(estado) == 0 else list(self.escenarios)

    def resultado(self, estado, accion, turno_max):
        return estado + (accion,)

    def es_terminal(self, estado):
        return len(estado) == 2

    def evaluar(self, estado):
        if estado not in self.resultados:
            modelo, escenario = estado
            Xtr, ytr, Xte, yte = aplicar_escenario(escenario, *self.datos, self.tarea, self.semilla)
            m = datos.pipeline(self.candidatos[modelo](), Xtr).fit(Xtr, ytr)
            self.resultados[estado] = float(self.scorer(m, Xte, yte))
            print(f"  entrenado  {modelo:<10} x {escenario:<20} score={self.resultados[estado]:.4f}")
        return self.resultados[estado]


def main():
    ap = argparse.ArgumentParser(description="Modelo mas robusto (minimax con poda alfa-beta).")
    ap.add_argument("csv")
    ap.add_argument("--objetivo", required=True)
    ap.add_argument("--tarea", choices=[datos.CLASIFICACION, datos.REGRESION])
    ap.add_argument("--sep", default=",")
    ap.add_argument("--modelos", nargs="+", choices=datos.MODELOS,
                    default=["bosque", "arbol", "lineal", "knn", "mlp"],
                    help="candidatos (conviene poner primero el que se espera mejor: poda mas)")
    ap.add_argument("--escenarios", nargs="+", choices=ESCENARIOS, default=ESCENARIOS)
    ap.add_argument("--mlp-config", help="mejor_individuo.json de mlp_genetico.py")
    ap.add_argument("--test", type=float, default=0.25)
    ap.add_argument("--excluir", nargs="*", default=[],
                    help="columnas que no son variables (ids, otros objetivos, metadatos)")
    ap.add_argument("--grupos", help="columna de la unidad que no se puede partir entre "
                                     "train y test (p. ej. enzyme_id)")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--salida", default="resultados_robustez")
    args = ap.parse_args()

    X, y, g = datos.cargar(args.csv, args.objetivo, args.sep, args.excluir, args.grupos)
    tarea = args.tarea or datos.detectar_tarea(y)
    metrica = datos.METRICA_DEFECTO[tarea]
    tr, te = datos.indices_division(X, y, tarea, args.test, args.semilla, g)
    X_train, X_test = X.iloc[tr], X.iloc[te]
    y_train, y_test = y.iloc[tr], y.iloc[te]

    candidatos = {n: (lambda n=n: datos.construir_modelo(n, tarea, args.semilla))
                  for n in args.modelos}
    if args.mlp_config:
        cfg = json.loads(Path(args.mlp_config).read_text())
        params = dict(hidden_layer_sizes=tuple(cfg["hidden_layer_sizes"]),
                      activation=cfg["activation"], alpha=cfg["alpha"],
                      learning_rate_init=cfg["learning_rate_init"], solver=cfg["solver"])
        candidatos["mlp_genetico"] = lambda: datos.construir_modelo("mlp", tarea, args.semilla, **params)

    print(f"Datos: {len(X)} filas, {X.shape[1]} columnas. Tarea: {tarea}. Metrica: {metrica}")
    print(f"Train={len(X_train)} Test={len(X_test)}"
          + (f", particion por '{args.grupos}' ({g.nunique()} grupos)" if g is not None else ""))
    if args.excluir:
        print(f"Columnas excluidas de X: {args.excluir}")
    print(f"Candidatos: {list(candidatos)}\nEscenarios: {args.escenarios}\n")

    t0 = time.time()
    juego = JuegoRobustez(candidatos, args.escenarios, X_train, y_train, X_test, y_test,
                          tarea, metrica, args.semilla)
    ganador, valor = mejor_accion(juego, (), profundidad=2, turno_max=True)

    total = len(candidatos) * len(args.escenarios)
    print(f"\n=== Peor caso por modelo ===")
    tabla = {}
    for m in candidatos:
        vistos = {e: s for (mm, e), s in juego.resultados.items() if mm == m}
        peor_e = min(vistos, key=vistos.get)
        podado = len(vistos) < len(args.escenarios)
        tabla[m] = {"peor_score": vistos[peor_e], "peor_escenario": peor_e,
                    "podado": podado, "scores": vistos}
        print(f"  {m:<13} {'<=' if podado else '= '} {vistos[peor_e]:.4f}  "
              f"(peor escenario: {peor_e}{', poda: resto sin entrenar' if podado else ''})")
    print(f"\nModelo mas robusto: {ganador}  (score garantizado {valor:.4f})")
    print(f"Entrenamientos: {len(juego.resultados)} de {total} "
          f"(la poda ahorro {total - len(juego.resultados)})  |  {time.time() - t0:.1f} s")

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    final = datos.pipeline(candidatos[ganador](), X_train).fit(X_train, y_train)
    dump(final, salida / "modelo_robusto.joblib")
    with open(salida / "robustez.json", "w") as f:
        json.dump({"ganador": ganador, "score_garantizado": valor, "metrica": metrica,
                   "entrenamientos": len(juego.resultados), "total_sin_poda": total,
                   "modelos": tabla}, f, indent=2)
    print(f"Resultados guardados en: {salida.resolve()}")


if __name__ == "__main__":
    main()
