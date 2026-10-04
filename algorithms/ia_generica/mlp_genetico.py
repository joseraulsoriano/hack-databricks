"""
Red neuronal (MLP de sklearn) con hiperparametros ajustados por un
Algoritmo Genetico. Entrena sobre cualquier dataset tabular (CSV).

Extraido de codigo/punto3_genetico_mlp.py y desacoplado del Dry Bean Dataset:
  - Clasificacion (MLPClassifier, F1-macro) o regresion (MLPRegressor, R2;
    la variable objetivo tambien se estandariza).
  - Columnas numericas -> imputacion + StandardScaler.
    Columnas categoricas -> imputacion + One-Hot. Se detectan solas (datos.py).
  - El preprocesamiento va DENTRO del pipeline, asi se ajusta solo con los
    folds de entrenamiento de cada validacion cruzada (sin fuga de datos).
  - Sobreajuste: early_stopping, alpha (L2) como gen, fitness por CV y
    evaluacion final solo sobre el test.

Uso desde consola:
    python3 mlp_genetico.py datos.csv --objetivo Class
    python3 mlp_genetico.py casas.csv --objetivo precio      # la tarea se detecta sola

Uso desde codigo:
    from mlp_genetico import ConfigGenetico, optimizar_mlp, construir_pipeline
    mejor, historial = optimizar_mlp(X_train, y_train, "clasificacion")
    modelo = construir_pipeline(mejor, X_train, "clasificacion").fit(X_train, y_train)
"""
import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.metrics import (accuracy_score, classification_report, f1_score,
                             mean_absolute_error, mean_squared_error, r2_score)
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline

import datos
from datos import CLASIFICACION, METRICA_DEFECTO, REGRESION


@dataclass
class ConfigGenetico:
    poblacion: int = 12
    max_generaciones: int = 10
    tasa_mutacion: float = 0.10
    porc_elite: float = 0.25
    paciencia: int = 4          # generaciones sin mejora antes de detenerse
    cv_folds: int = 3
    max_iter: int = 200         # epocas maximas de cada MLP
    semilla: int = 42
    n_jobs: int = -1


# Dominio de cada gen. Enteros -> (min, max); log -> (min, max) en escala log;
# listas -> opciones categoricas.
DOMINIO_DEFECTO = {
    "capa1": (5, 200),
    "capa2": (0, 200),          # 0 = la capa no existe
    "capa3": (0, 150),
    "activation": ["relu", "tanh", "logistic"],
    "alpha": (1e-5, 1e-1),                  # escala log
    "learning_rate_init": (1e-4, 1e-1),     # escala log
    "solver": ["adam", "sgd"],
}
GENES_LOG = {"alpha", "learning_rate_init"}


# ---------------------------------------------------------------------------
# Representacion del individuo y operadores geneticos
# ---------------------------------------------------------------------------

def _gen_aleatorio(gen, dominio, rng: random.Random):
    valores = dominio[gen]
    if isinstance(valores, list):
        return rng.choice(valores)
    lo, hi = valores
    if gen in GENES_LOG:
        return float(10 ** rng.uniform(np.log10(lo), np.log10(hi)))
    return rng.randint(lo, hi)


def reparar(individuo: dict) -> dict:
    """capa3 solo tiene sentido si capa2 > 0."""
    if individuo.get("capa2", 0) == 0 and "capa3" in individuo:
        individuo["capa3"] = 0
    return individuo


def generar_individuo(dominio, rng) -> dict:
    return reparar({gen: _gen_aleatorio(gen, dominio, rng) for gen in dominio})


def cruza(padre1: dict, padre2: dict, rng) -> dict:
    """Cruza uniforme: cada gen se hereda al azar de uno de los dos padres."""
    return reparar({gen: (padre1 if rng.random() < 0.5 else padre2)[gen] for gen in padre1})


def mutar(individuo: dict, dominio, tasa, rng) -> dict:
    ind = dict(individuo)
    for gen in dominio:
        if rng.random() < tasa:
            ind[gen] = _gen_aleatorio(gen, dominio, rng)
    return reparar(ind)


def hidden_layer_sizes(individuo: dict) -> tuple:
    capas = [individuo.get(f"capa{i}", 0) for i in (1, 2, 3)]
    return tuple(c for c in capas if c > 0)


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

def construir_pipeline(individuo: dict, X: pd.DataFrame, tarea: str,
                       max_iter: int = 200, semilla: int = 42) -> Pipeline:
    mlp = datos.construir_modelo(
        "mlp", tarea, semilla,
        hidden_layer_sizes=hidden_layer_sizes(individuo),
        activation=individuo["activation"],
        alpha=individuo["alpha"],
        learning_rate_init=individuo["learning_rate_init"],
        solver=individuo["solver"],
        early_stopping=True,          # previene sobreajuste
        n_iter_no_change=10,
        validation_fraction=0.1,
        max_iter=max_iter,
    )
    return datos.pipeline(mlp, X)


def fitness(individuo, X, y, tarea, metrica, config: ConfigGenetico, cache: dict,
            grupos=None, cv_split=None) -> float:
    """Metrica promedio en validacion cruzada, SOLO sobre train."""
    clave = tuple(sorted(individuo.items()))
    if clave in cache:
        return cache[clave]

    modelo = construir_pipeline(individuo, X, tarea, config.max_iter, config.semilla)
    cv = datos.particion_cv(tarea, config.cv_folds, config.semilla, grupos, cv_split)
    scores = cross_val_score(modelo, X, y, cv=cv, scoring=metrica, groups=grupos,
                             n_jobs=config.n_jobs)
    # Si un individuo diverge (NaN) se descarta en vez de romper el ordenamiento.
    valor = float(np.nanmean(scores)) if not np.all(np.isnan(scores)) else -np.inf
    cache[clave] = valor
    return valor


# ---------------------------------------------------------------------------
# Bucle principal del Algoritmo Genetico
# ---------------------------------------------------------------------------

def optimizar_mlp(X: pd.DataFrame, y, tarea: str = CLASIFICACION,
                  config: ConfigGenetico = None, dominio: dict = None,
                  metrica: str = None, verbose: bool = True,
                  grupos=None, cv_split=None):
    """Busca los mejores hiperparametros. Devuelve (mejor_individuo, historial).

    grupos / cv_split: ver datos.particion_cv. Con datos donde la misma unidad
    aparece en varias filas, sin esto el fitness sale inflado."""
    config = config or ConfigGenetico()
    dominio = dominio or DOMINIO_DEFECTO
    metrica = metrica or METRICA_DEFECTO[tarea]
    rng = random.Random(config.semilla)
    cache = {}

    poblacion = [generar_individuo(dominio, rng) for _ in range(config.poblacion)]
    mejor_global, mejor_individuo = -np.inf, None
    sin_mejora = 0
    historial = []

    for generacion in range(1, config.max_generaciones + 1):
        fits = [fitness(ind, X, y, tarea, metrica, config, cache, grupos, cv_split)
                for ind in poblacion]
        orden = np.argsort(fits)[::-1]
        poblacion = [poblacion[i] for i in orden]
        fits = [fits[i] for i in orden]

        validos = [f for f in fits if np.isfinite(f)]
        promedio = float(np.mean(validos)) if validos else float("nan")
        historial.append({"generacion": generacion, "mejor_fitness": fits[0],
                          "fitness_promedio": promedio})
        if verbose:
            print(f"Gen {generacion:2d}: mejor={fits[0]:.4f}  promedio={promedio:.4f}  "
                  f"individuo={poblacion[0]}")

        if fits[0] > mejor_global:
            mejor_global, mejor_individuo = fits[0], dict(poblacion[0])
            sin_mejora = 0
        else:
            sin_mejora += 1
        if sin_mejora >= config.paciencia:
            if verbose:
                print(f"Convergencia (sin mejora en {config.paciencia} generaciones).")
            break

        n_elite = min(len(poblacion), max(2, int(config.poblacion * config.porc_elite)))
        elite = poblacion[:n_elite]
        nueva = [dict(ind) for ind in elite]            # elitismo: pasan intactos
        while len(nueva) < config.poblacion:
            # Con una elite de 1 (poblacion minima) no hay con quien cruzar: solo muta.
            p1, p2 = rng.sample(elite, 2) if len(elite) >= 2 else (elite[0], elite[0])
            nueva.append(mutar(cruza(p1, p2, rng), dominio, config.tasa_mutacion, rng))
        poblacion = nueva

    if mejor_individuo is None:
        raise RuntimeError(
            "Ningun individuo dio un fitness valido: todas las redes divergieron. "
            "Revisa el objetivo y la metrica, o sube --generaciones.")
    return mejor_individuo, historial


def evaluar(modelo, X_train, y_train, X_test, y_test, tarea) -> dict:
    """Metricas en train y test (la brecha indica sobreajuste)."""
    p_train, p_test = modelo.predict(X_train), modelo.predict(X_test)
    if tarea == CLASIFICACION:
        m = {
            "accuracy_train": accuracy_score(y_train, p_train),
            "accuracy_test": accuracy_score(y_test, p_test),
            "f1_macro_train": f1_score(y_train, p_train, average="macro"),
            "f1_macro_test": f1_score(y_test, p_test, average="macro"),
        }
        m["brecha_f1_macro"] = m["f1_macro_train"] - m["f1_macro_test"]
    else:
        m = {
            "r2_train": r2_score(y_train, p_train),
            "r2_test": r2_score(y_test, p_test),
            "mae_test": mean_absolute_error(y_test, p_test),
            "rmse_test": float(np.sqrt(mean_squared_error(y_test, p_test))),
        }
        m["brecha_r2"] = m["r2_train"] - m["r2_test"]
    mlp = modelo.named_steps["modelo"]
    mlp = getattr(mlp, "regressor_", mlp)
    m["n_iter_entrenamiento"] = int(mlp.n_iter_)
    m["detuvo_por_early_stopping"] = bool(mlp.n_iter_ < mlp.max_iter)
    return {k: float(v) if isinstance(v, (np.floating, float)) else v for k, v in m.items()}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="MLP + Algoritmo Genetico sobre un CSV.")
    ap.add_argument("csv", help="archivo de datos (CSV con encabezados)")
    ap.add_argument("--objetivo", required=True, help="columna a predecir")
    ap.add_argument("--tarea", choices=[CLASIFICACION, REGRESION],
                    help="por defecto se detecta sola")
    ap.add_argument("--test", type=float, default=0.25, help="proporcion de test")
    ap.add_argument("--sep", default=",", help="separador del CSV")
    ap.add_argument("--salida", default="resultados_mlp", help="carpeta de resultados")
    ap.add_argument("--poblacion", type=int, default=ConfigGenetico.poblacion)
    ap.add_argument("--generaciones", type=int, default=ConfigGenetico.max_generaciones)
    ap.add_argument("--cv", type=int, default=ConfigGenetico.cv_folds)
    ap.add_argument("--excluir", nargs="*", default=[],
                    help="columnas que no son variables (ids, otros objetivos, metadatos)")
    ap.add_argument("--grupos", help="columna de la unidad que no se puede partir entre "
                                     "train, test y folds (p. ej. enzyme_id)")
    ap.add_argument("--cv-split", help="columna con el fold ya asignado a cada fila "
                                       "(particion fija publicada con los datos)")
    ap.add_argument("--semilla", type=int, default=ConfigGenetico.semilla)
    args = ap.parse_args()

    excluir = list(args.excluir) + ([args.cv_split] if args.cv_split else [])
    X, y, g = datos.cargar(args.csv, args.objetivo, args.sep, excluir, args.grupos)
    args.tarea = args.tarea or datos.detectar_tarea(y)
    folds = pd.read_csv(args.csv, sep=args.sep)[args.cv_split].loc[X.index] if args.cv_split else None

    tr, te = datos.indices_division(X, y, args.tarea, args.test, args.semilla, g)
    X_train, X_test = X.iloc[tr], X.iloc[te]
    y_train, y_test = y.iloc[tr], y.iloc[te]
    g_train = g.iloc[tr] if g is not None else None
    folds_train = folds.iloc[tr] if folds is not None else None
    config = ConfigGenetico(poblacion=args.poblacion, max_generaciones=args.generaciones,
                            cv_folds=args.cv, semilla=args.semilla)

    print(f"Datos: {len(X)} filas, {X.shape[1]} variables. Tarea: {args.tarea}. "
          f"Train={len(X_train)} Test={len(X_test)}")
    if args.excluir:
        print(f"Columnas excluidas de X: {args.excluir}")
    if args.cv_split:
        print(f"Validacion cruzada: particion fija '{args.cv_split}' "
              f"({folds_train.nunique()} folds en train)")
    elif g is not None:
        print(f"Particion y validacion cruzada agrupadas por '{args.grupos}' "
              f"({g.nunique()} grupos, {g_train.nunique()} en train)")
    print()
    mejor, historial = optimizar_mlp(X_train, y_train, args.tarea, config,
                                     grupos=g_train, cv_split=folds_train)
    print(f"\nMejor individuo: {mejor}  -> capas {hidden_layer_sizes(mejor)}")

    modelo = construir_pipeline(mejor, X_train, args.tarea, config.max_iter, config.semilla)
    modelo.fit(X_train, y_train)
    metricas = evaluar(modelo, X_train, y_train, X_test, y_test, args.tarea)
    print("\n=== Metricas finales ===")
    for k, v in metricas.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(historial).to_csv(salida / "historial_fitness.csv", index=False)
    with open(salida / "mejor_individuo.json", "w") as f:
        json.dump({**mejor, "hidden_layer_sizes": list(hidden_layer_sizes(mejor))}, f, indent=2)
    with open(salida / "metricas.json", "w") as f:
        json.dump({"config": asdict(config), **metricas}, f, indent=2)
    if args.tarea == CLASIFICACION:
        reporte = classification_report(y_test, modelo.predict(X_test))
        (salida / "reporte_clasificacion.txt").write_text(reporte)
        print("\n" + reporte)
    dump(modelo, salida / "modelo.joblib")    # incluye el preprocesamiento
    print(f"Resultados guardados en: {salida.resolve()}")


if __name__ == "__main__":
    main()
