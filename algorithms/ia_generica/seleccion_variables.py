"""
Seleccion de variables de un dataset con IDA* (busqueda en arbol).

Pregunta que responde: ¿cual es el conjunto MINIMO de columnas con el que el
modelo conserva su rendimiento? El resultado es un dataset reducido.

Modelado como busqueda (ver ida_estrella.py):
  - Estado:     tupla ordenada de indices de columnas elegidas (raiz = vacia).
  - Accion:     agregar una columna (solo con indice mayor al ultimo, asi
                cada subconjunto aparece una sola vez en el arbol).
  - Costo:      1 por columna  ->  g(n) = numero de columnas elegidas.
  - Meta:       score de validacion cruzada >= umbral. Por defecto el umbral
                es el score con TODAS las columnas menos una tolerancia.
  - h(n):       0 si ya alcanza el umbral, 1 si no (falta al menos una
                columna). Es admisible, asi que el subconjunto es minimo.
  - Orden:      los hijos se exploran del mejor score al peor.

Cada score se calcula una sola vez (cache). El numero de evaluaciones crece
combinatoriamente con el tamaño del subconjunto: --max-variables lo acota y
--muestra reduce las filas usadas durante la busqueda.

Uso:
    python3 seleccion_variables.py datos.csv --objetivo Class
    python3 seleccion_variables.py datos.csv --objetivo precio --tolerancia 0.02 \\
        --guardar datos_reducidos.csv
"""
import argparse
import json
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import cross_val_score

import datos
from ida_estrella import ProblemaBusqueda, ida_estrella


class SeleccionVariables(ProblemaBusqueda):
    def __init__(self, X, y, tarea, modelo="arbol", metrica=None, cv=3,
                 umbral=None, tolerancia=0.01, max_variables=None, semilla=42,
                 grupos=None, cv_split=None):
        self.X, self.y, self.tarea = X, y, tarea
        self.columnas = list(X.columns)
        self.modelo, self.semilla = modelo, semilla
        self.metrica = metrica or datos.METRICA_DEFECTO[tarea]
        self.grupos = grupos
        self.cv = datos.particion_cv(tarea, cv, semilla, grupos, cv_split)
        self.max_variables = max_variables or len(self.columnas)
        self._cache = {}
        self._desv = {}
        self.score_completo = self.score(tuple(range(len(self.columnas))))
        self.umbral = umbral if umbral is not None else self.score_completo - tolerancia

    def score(self, estado) -> float:
        """Score de validacion cruzada usando solo las columnas de 'estado'."""
        if not estado:
            return -np.inf
        if estado not in self._cache:
            Xs = self.X.iloc[:, list(estado)]
            modelo = datos.pipeline(datos.construir_modelo(self.modelo, self.tarea, self.semilla), Xs)
            s = cross_val_score(modelo, Xs, self.y, cv=self.cv, scoring=self.metrica,
                                groups=self.grupos, n_jobs=-1)
            self._cache[estado] = float(np.nanmean(s))
            self._desv[estado] = float(np.nanstd(s))
        return self._cache[estado]

    def desviacion(self, estado) -> float:
        """Desviacion entre folds del ultimo score calculado para 'estado'."""
        self.score(estado)
        return self._desv[estado]

    @property
    def evaluaciones(self) -> int:
        return len(self._cache)

    # --- Interfaz ProblemaBusqueda ----------------------------------------
    def estado_inicial(self):
        return ()

    def es_meta(self, estado):
        return bool(estado) and self.score(estado) >= self.umbral

    def heuristica(self, estado):
        return 0 if self.es_meta(estado) else 1

    def sucesores(self, estado):
        if len(estado) >= self.max_variables:
            return []
        desde = estado[-1] + 1 if estado else 0
        hijos = [estado + (i,) for i in range(desde, len(self.columnas))]
        hijos.sort(key=self.score, reverse=True)       # mejor score primero
        return [(self.columnas[h[-1]], h, 1) for h in hijos]


def main():
    ap = argparse.ArgumentParser(description="Conjunto minimo de columnas con IDA*.")
    ap.add_argument("csv")
    ap.add_argument("--objetivo", required=True, help="columna a predecir")
    ap.add_argument("--tarea", choices=[datos.CLASIFICACION, datos.REGRESION],
                    help="por defecto se detecta sola")
    ap.add_argument("--sep", default=",")
    ap.add_argument("--modelo", choices=datos.MODELOS, default="arbol",
                    help="modelo usado para evaluar cada subconjunto (rapido = mejor)")
    ap.add_argument("--tolerancia", type=float, default=0.01,
                    help="cuanto score se acepta perder frente a usar todas las columnas")
    ap.add_argument("--umbral", type=float, help="score minimo exigido (ignora --tolerancia)")
    ap.add_argument("--max-variables", type=int, help="tamaño maximo del subconjunto")
    ap.add_argument("--muestra", type=int, default=5000,
                    help="filas usadas en la busqueda (0 = todas)")
    ap.add_argument("--cv", type=int, default=3)
    ap.add_argument("--excluir", nargs="*", default=[],
                    help="columnas que no son variables (ids, otros objetivos, metadatos)")
    ap.add_argument("--grupos", help="columna de la unidad que no se puede partir entre "
                                     "folds (p. ej. enzyme_id)")
    ap.add_argument("--cv-split", help="columna con el fold ya asignado a cada fila "
                                       "(particion fija publicada con los datos)")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--guardar", help="CSV de salida con solo las columnas elegidas + objetivo")
    args = ap.parse_args()

    excluir = list(args.excluir) + ([args.cv_split] if args.cv_split else [])
    X, y, g = datos.cargar(args.csv, args.objetivo, args.sep, excluir, args.grupos)
    tarea = args.tarea or datos.detectar_tarea(y)
    folds = pd.read_csv(args.csv, sep=args.sep)[args.cv_split] if args.cv_split else None
    if folds is not None:
        folds = folds.loc[X.index]

    Xb, yb, gb, foldsb = X, y, g, folds
    if args.muestra and len(X) > args.muestra:
        sub, _ = datos.indices_division(X, y, tarea, test=1 - args.muestra / len(X),
                                        semilla=args.semilla, grupos=g)
        Xb, yb = X.iloc[sub], y.iloc[sub]
        gb = g.iloc[sub] if g is not None else None
        foldsb = folds.iloc[sub] if folds is not None else None

    t0 = time.time()
    problema = SeleccionVariables(Xb, yb, tarea, args.modelo, cv=args.cv, umbral=args.umbral,
                                  tolerancia=args.tolerancia, max_variables=args.max_variables,
                                  semilla=args.semilla, grupos=gb, cv_split=foldsb)
    print(f"Datos: {len(X)} filas ({len(Xb)} en la busqueda), {X.shape[1]} columnas. "
          f"Tarea: {tarea}. Modelo: {args.modelo}. Metrica: {problema.metrica}")
    if args.excluir:
        print(f"Columnas excluidas de X: {args.excluir}")
    if args.cv_split:
        print(f"Validacion cruzada: particion fija '{args.cv_split}' "
              f"({foldsb.nunique()} folds)")
    elif g is not None:
        print(f"Validacion cruzada: agrupada por '{args.grupos}' ({gb.nunique()} grupos)")
    print(f"Score con todas las columnas: {problema.score_completo:.4f} "
          f"+/- {problema.desviacion(tuple(range(X.shape[1]))):.4f}  "
          f"-> umbral: {problema.umbral:.4f}\n")

    res = ida_estrella(problema)
    if res is None:
        print("Ningun subconjunto alcanza el umbral (prueba con mas --tolerancia "
              "o mas --max-variables).")
        return

    elegidas = list(res.acciones)
    final = tuple(problema.columnas.index(c) for c in elegidas)
    print(f"Columnas elegidas ({len(elegidas)} de {X.shape[1]}): {elegidas}")
    print(f"Score con esas columnas: {problema.score(final):.4f} "
          f"+/- {problema.desviacion(final):.4f}")
    print(f"Subconjuntos evaluados: {problema.evaluaciones}  |  iteraciones IDA*: "
          f"{res.iteraciones}  |  {time.time() - t0:.1f} s")
    descartadas = [c for c in X.columns if c not in elegidas]
    print(f"Columnas descartadas: {descartadas}")

    if args.guardar:
        # Se conservan la columna de grupos y la de folds: los pasos 2 y 3 las necesitan.
        salida = X[elegidas].assign(**{args.objetivo: y})
        if g is not None:
            salida[args.grupos] = g
        if folds is not None:
            salida[args.cv_split] = folds
        salida.to_csv(args.guardar, index=False)
        with open(args.guardar.rsplit(".", 1)[0] + "_seleccion.json", "w") as f:
            json.dump({"columnas_elegidas": elegidas, "descartadas": descartadas,
                       "score_elegidas": problema.score(final),
                       "desv_elegidas": problema.desviacion(final),
                       "score_todas": problema.score_completo, "umbral": problema.umbral,
                       "modelo": args.modelo, "metrica": problema.metrica,
                       "grupos": args.grupos, "cv_split": args.cv_split,
                       "excluidas": args.excluir}, f, indent=2)
        print(f"Dataset reducido guardado en: {args.guardar}")


if __name__ == "__main__":
    main()
