"""Los tres algoritmos de busqueda/IA sobre workspace.lab.pet_activity_ml.

Pregunta del lab: que propiedades de una PET hidrolasa predicen su actividad.
Esto es analisis estadistico sobre datos ya publicados (Zenodo 10.5281/zenodo.15417757,
CC-BY-4.0, Norton-Baker et al. 2025). No disena secuencias ni propone modificaciones.

Pasos (motores en ../ia_generica):
  1  IDA*               conjunto minimo de descriptores que conserva el F1-macro
  2  MLP + genetico     hiperparametros de la red
  3  Minimax alfa-beta  modelo con mejor peor-caso (ruido, huecos, pocos datos)

Lo que este script impone, porque la tabla lo exige (ver algorithms/README.md):

  - Particion FIJA por `cv_split`, la publicada con el paper. Un split aleatorio
    mete la misma enzima en train y validacion: medido, infla el F1-macro de
    0.72 a 0.86.
  - Se excluyen de X: `enzyme_id` (identificador), `cv_split` (metadato de la
    particion), el objetivo que no se predice (`activity` <-> `is_active`, cada
    uno deriva del otro) y `design_round`/`temporal_split`, que describen el
    proceso de diseno y no la enzima.
  - Tarea EXPLICITA: `is_active` es clasificacion, `activity` regresion.
  - Metrica macro-F1 por el desbalance 29/71. Accuracy no sirve: decir siempre
    "no activa" acierta el 71%.
  - Todo se reporta como media +/- desviacion entre folds, con el gap train/val.

Uso:
    uv run python -m algorithms.pet_activity_pipeline                 # los 3 pasos
    uv run python -m algorithms.pet_activity_pipeline --pasos 1
    uv run python -m algorithms.pet_activity_pipeline --objetivo activity
    uv run python -m algorithms.pet_activity_pipeline --fuente databricks
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.model_selection import PredefinedSplit, cross_validate

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "data" / "datasets" / "pet_activity"
SALIDA = ROOT / "data" / "resultados" / "algorithms"
TABLA = "workspace.lab.pet_activity_ml"

GRUPO = "enzyme_id"          # una enzima, hasta 11 condiciones medidas
FOLDS = "cv_split"           # particion de 5 folds publicada con los datos
OBJETIVOS = {"is_active": "clasificacion", "activity": "regresion"}
# Columnas que no describen a la enzima y no pueden entrar como variables.
# _rescued_data la agrega read_files de Databricks (texto que no pudo interpretar).
METADATOS = ["design_round", "temporal_split", "_rescued_data"]


# ---------------------------------------------------------------------------
# ia_generica: los motores viven fuera del repo (IDA*, alfa-beta, genetico)
# ---------------------------------------------------------------------------

def importar_ia_generica(ruta: str | None):
    """Agrega ia_generica al sys.path y devuelve sus modulos."""
    candidatas = [Path(p) for p in (ruta, os.environ.get("IA_GENERICA"),
                                    Path(__file__).parent / "ia_generica", ROOT.parent / "ia_generica") if p]
    for c in candidatas:
        if (c / "datos.py").exists():
            sys.path.insert(0, str(c.resolve()))
            import datos
            import mlp_genetico
            import seleccion_robusta
            import seleccion_variables
            from ida_estrella import ida_estrella
            return datos, seleccion_variables, mlp_genetico, seleccion_robusta, ida_estrella
    raise SystemExit(
        "No encuentro ia_generica (los motores IDA*, genetico y alfa-beta).\n"
        f"Busque en: {[str(c) for c in candidatas]}\n"
        "Pasa --ia-generica RUTA o exporta IA_GENERICA=/ruta/a/ia_generica")


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

def construir_vista_local() -> pd.DataFrame:
    """Reproduce la vista pet_activity_ml uniendo los dos CSV locales."""
    act, feat = LOCAL / "pet_activity.csv", LOCAL / "enzyme_features.csv"
    if not act.exists() or not feat.exists():
        raise SystemExit(
            f"Faltan los CSV en {LOCAL}. Generalos con:\n"
            "  uv run python -m data_pipeline.datasets.pet_activity")
    a = pd.read_csv(act).drop(columns=["source_doc_id"], errors="ignore")
    f = pd.read_csv(feat).drop(
        columns=["sequence", "cv_split", "has_nonzero_activity", "max_observed_activity"],
        errors="ignore")
    return a.merge(f, on=GRUPO, how="inner")


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    """La API de SQL devuelve todo como texto: booleanos y numeros a su tipo."""
    for c in df.columns:
        valores = set(df[c].dropna().str.lower().unique())
        if valores and valores <= {"true", "false"}:
            df[c] = df[c].str.lower() == "true"
        else:
            convertida = pd.to_numeric(df[c], errors="coerce")
            if convertida.notna().sum() == df[c].notna().sum():
                df[c] = convertida
    return df


def cargar_tabla(fuente: str) -> tuple[pd.DataFrame, str]:
    """Devuelve (df, origen). 'auto' intenta Databricks y cae a local."""
    if fuente in ("auto", "databricks"):
        try:
            from data_pipeline.databricks_io import Databricks
            db = Databricks()
            # sql() devuelve listas de strings sin nombres de columna: se piden aparte.
            cols = [r[0] for r in db.sql(f"DESCRIBE {TABLA}") if r[0] and not r[0].startswith("#")]
            filas = db.sql(f"SELECT {', '.join(f'`{c}`' for c in cols)} FROM {TABLA}")
            total = int(db.sql(f"SELECT count(*) FROM {TABLA}")[0][0])
            if len(filas) != total:   # el resultado en linea puede venir partido en chunks
                raise RuntimeError(f"llegaron {len(filas)} de {total} filas")
            return _tipar(pd.DataFrame(filas, columns=cols)), f"databricks:{TABLA}"
        except Exception as e:
            if fuente == "databricks":
                raise SystemExit(f"No pude leer {TABLA}: {e}")
            print(f"[aviso] Databricks no disponible ({type(e).__name__}); uso los CSV locales.")
    return construir_vista_local(), f"local:{LOCAL}"


def preparar(df: pd.DataFrame, objetivo: str):
    """Separa X (solo descriptores y condicion del ensayo), y, grupos y folds."""
    otro_objetivo = [c for c in OBJETIVOS if c != objetivo]
    fuera = [objetivo, GRUPO, FOLDS, *otro_objetivo, *METADATOS]
    faltan = [c for c in (objetivo, GRUPO, FOLDS) if c not in df.columns]
    if faltan:
        raise SystemExit(f"La tabla no trae {faltan}. Columnas: {list(df.columns)}")
    df = df.dropna(subset=[objetivo])
    X = df.drop(columns=[c for c in dict.fromkeys(fuera) if c in df.columns])
    y = df[objetivo].astype(int) if objetivo == "is_active" else df[objetivo].astype(float)
    return X, y, df[GRUPO], df[FOLDS], [c for c in fuera if c != objetivo and c in df.columns]


# ---------------------------------------------------------------------------
# Evaluacion: siempre con la particion fija, siempre media +/- desviacion
# ---------------------------------------------------------------------------

def evaluar_cv(modelo, X, y, folds, metrica) -> dict:
    """Media +/- desviacion entre folds, y el gap train/val (sobreajuste)."""
    r = cross_validate(modelo, X, y, cv=PredefinedSplit(folds), scoring=metrica,
                       return_train_score=True, n_jobs=-1, error_score="raise")
    val, tr = r["test_score"], r["train_score"]
    return {"media": float(np.mean(val)), "desv": float(np.std(val)),
            "train": float(np.mean(tr)), "gap": float(np.mean(tr) - np.mean(val)),
            "folds": [round(float(s), 4) for s in val]}


def linea(nombre, r, metrica, extra="") -> str:
    return (f"  {nombre:<28} {metrica} = {r['media']:.4f} +/- {r['desv']:.4f}"
            f"   (train {r['train']:.4f}, gap {r['gap']:+.4f}) {extra}")


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------

def paso1(mod, X, y, tarea, folds, grupos, metrica, args) -> dict:
    """IDA*: conjunto minimo de columnas que conserva el score."""
    datos, sv, _, _, ida_estrella = mod
    print(f"\n=== Paso 1 - IDA*: conjunto minimo de columnas "
          f"(tolerancia {args.tolerancia}, max {args.max_variables}) ===")
    problema = sv.SeleccionVariables(
        X, y, tarea, modelo=args.modelo_busqueda, metrica=metrica,
        tolerancia=args.tolerancia, max_variables=args.max_variables,
        semilla=args.semilla, grupos=grupos, cv_split=folds)
    t0 = time.time()
    print(f"  score con las {X.shape[1]} columnas: {problema.score_completo:.4f} "
          f"+/- {problema.desviacion(tuple(range(X.shape[1]))):.4f}"
          f"  -> umbral {problema.umbral:.4f}")
    res = ida_estrella(problema)
    if res is None:
        print("  ningun subconjunto alcanza el umbral: sube --tolerancia o --max-variables")
        return {"elegidas": None, "evaluaciones": problema.evaluaciones}
    elegidas = list(res.acciones)
    idx = tuple(problema.columnas.index(c) for c in elegidas)
    print(f"  elegidas ({len(elegidas)} de {X.shape[1]}): {elegidas}")
    print(f"  score: {problema.score(idx):.4f} +/- {problema.desviacion(idx):.4f}")
    print(f"  subconjuntos evaluados: {problema.evaluaciones} | iteraciones: {res.iteraciones}"
          f" | {time.time() - t0:.1f} s")
    return {"elegidas": elegidas, "score": problema.score(idx),
            "desv": problema.desviacion(idx), "score_todas": problema.score_completo,
            "umbral": problema.umbral, "evaluaciones": problema.evaluaciones,
            "iteraciones": res.iteraciones, "segundos": round(time.time() - t0, 1)}


def paso2(mod, X, y, tarea, folds, grupos, metrica, args) -> dict:
    """Genetico: hiperparametros del MLP. Fitness por CV con la particion fija."""
    datos, _, mg, _, _ = mod
    print(f"\n=== Paso 2 - Red neuronal + algoritmo genetico "
          f"(poblacion {args.poblacion}, {args.generaciones} generaciones) ===")
    cfg = mg.ConfigGenetico(poblacion=args.poblacion, max_generaciones=args.generaciones,
                            semilla=args.semilla)
    t0 = time.time()
    mejor, historial = mg.optimizar_mlp(X, y, tarea, cfg, metrica=metrica,
                                        grupos=grupos, cv_split=folds)
    modelo = mg.construir_pipeline(mejor, X, tarea, cfg.max_iter, cfg.semilla)
    r = evaluar_cv(modelo, X, y, folds, metrica)
    print(f"  mejor individuo: capas {mg.hidden_layer_sizes(mejor)}, {mejor['activation']}, "
          f"solver {mejor['solver']}, alpha {mejor['alpha']:.2e}")
    print(linea("mlp_genetico", r, metrica, f"| {time.time() - t0:.1f} s"))
    return {"mejor_individuo": {**mejor, "hidden_layer_sizes": list(mg.hidden_layer_sizes(mejor))},
            "cv": r, "generaciones_corridas": len(historial),
            "segundos": round(time.time() - t0, 1)}


def anidada(mod, X, y, tarea, folds, grupos, metrica, args) -> dict:
    """Validacion cruzada ANIDADA sobre los folds de cv_split.

    En la evaluacion normal, el genetico (y el IDA*) eligen usando los mismos
    folds con los que despues se mide: el score sale optimista. Aqui, por cada
    fold externo, la eleccion se hace SOLO con los otros folds (CV interna) y
    el fold externo se usa una unica vez, para medir.
    """
    datos, sv, mg, _, ida_estrella = mod
    from sklearn.metrics import get_scorer
    scorer = get_scorer(metrica)
    print(f"\n=== Validacion cruzada anidada ({folds.nunique()} folds externos"
          f"{', con seleccion de variables' if args.anidar_seleccion else ''}) ===")
    t0 = time.time()
    res = {"mlp_genetico": [], "bosque": [], "bosque_seleccion": [],
           "individuos": [], "columnas": []}
    for f in sorted(folds.unique()):
        tr, te = (folds != f).to_numpy(), (folds == f).to_numpy()
        Xtr, Xte, ytr, yte = X[tr], X[te], y[tr], y[te]
        ftr, gtr = folds[tr], grupos[tr]

        # Bosque sin ajuste: referencia en las mismas particiones externas.
        b = datos.pipeline(datos.construir_modelo("bosque", tarea, args.semilla), Xtr).fit(Xtr, ytr)
        res["bosque"].append(float(scorer(b, Xte, yte)))

        # Genetico: elige con la CV interna (los otros folds) y se mide en f.
        cfg = mg.ConfigGenetico(poblacion=args.poblacion, max_generaciones=args.generaciones,
                                semilla=args.semilla)
        mejor, _ = mg.optimizar_mlp(Xtr, ytr, tarea, cfg, metrica=metrica, verbose=False,
                                    grupos=gtr, cv_split=ftr)
        m = mg.construir_pipeline(mejor, Xtr, tarea, cfg.max_iter, cfg.semilla).fit(Xtr, ytr)
        res["mlp_genetico"].append(float(scorer(m, Xte, yte)))
        res["individuos"].append({**mejor, "hidden_layer_sizes": list(mg.hidden_layer_sizes(mejor))})
        linea_fold = (f"  fold {f}: bosque {res['bosque'][-1]:.4f} | "
                      f"mlp_genetico {res['mlp_genetico'][-1]:.4f} "
                      f"(capas {mg.hidden_layer_sizes(mejor)}, {mejor['solver']})")

        if args.anidar_seleccion:
            p = sv.SeleccionVariables(Xtr, ytr, tarea, modelo=args.modelo_busqueda, metrica=metrica,
                                      tolerancia=args.tolerancia, max_variables=args.max_variables,
                                      semilla=args.semilla, grupos=gtr, cv_split=ftr)
            r = ida_estrella(p)
            # Sin subconjunto que alcance el umbral se usan todas las columnas para
            # medir, pero NO cuenta como eleccion en la frecuencia de columnas.
            cols = list(r.acciones) if r else list(X.columns)
            if r is None:
                res.setdefault("sin_subconjunto", []).append(int(f))
            bs = datos.pipeline(datos.construir_modelo("bosque", tarea, args.semilla),
                                Xtr[cols]).fit(Xtr[cols], ytr)
            res["bosque_seleccion"].append(float(scorer(bs, Xte[cols], yte)))
            res["columnas"].append(list(r.acciones) if r else None)
            linea_fold += f" | IDA* {res['bosque_seleccion'][-1]:.4f} {cols}"
        print(linea_fold + f"  [{time.time() - t0:.0f} s]")

    resumen = {}
    for k in ("bosque", "mlp_genetico", "bosque_seleccion"):
        if res[k]:
            resumen[k] = {"media": float(np.mean(res[k])), "desv": float(np.std(res[k])),
                          "folds": [round(s, 4) for s in res[k]]}
    if res["columnas"]:
        from collections import Counter
        frec = Counter(c for cols in res["columnas"] if cols for c in cols)
        resumen["frecuencia_columnas"] = dict(frec.most_common())
        resumen["folds_sin_subconjunto"] = res.get("sin_subconjunto", [])
    resumen["individuos"] = res["individuos"]
    dif = np.array(res["mlp_genetico"]) - np.array(res["bosque"])
    resumen["mlp_menos_bosque"] = {"media": float(dif.mean()), "desv": float(dif.std()),
                                   "folds_gana_mlp": int((dif > 0).sum()), "folds": len(dif)}
    resumen["segundos"] = round(time.time() - t0, 1)
    return resumen


def paso3(mod, X, y, tarea, folds, grupos, metrica, args, mlp_cfg=None) -> dict:
    """Minimax alfa-beta: el modelo con mejor peor-caso.

    Train/test = la particion fija. El fold mas alto es el test, el resto train:
    asi ninguna enzima aparece en los dos lados.
    """
    datos, _, _, sr, _ = mod
    print(f"\n=== Paso 3 - Minimax con poda alfa-beta: modelo mas robusto ===")
    test_fold = sorted(folds.unique())[-1]
    tr, te = (folds != test_fold).to_numpy(), (folds == test_fold).to_numpy()
    X_train, X_test, y_train, y_test = X[tr], X[te], y[tr], y[te]
    print(f"  train = folds != {test_fold} ({tr.sum()} filas), "
          f"test = fold {test_fold} ({te.sum()} filas)")

    candidatos = {n: (lambda n=n: datos.construir_modelo(n, tarea, args.semilla))
                  for n in args.modelos}
    if mlp_cfg:
        p = dict(hidden_layer_sizes=tuple(mlp_cfg["hidden_layer_sizes"]),
                 activation=mlp_cfg["activation"], alpha=mlp_cfg["alpha"],
                 learning_rate_init=mlp_cfg["learning_rate_init"], solver=mlp_cfg["solver"])
        candidatos["mlp_genetico"] = lambda: datos.construir_modelo("mlp", tarea, args.semilla, **p)

    t0 = time.time()
    juego = sr.JuegoRobustez(candidatos, sr.ESCENARIOS, X_train, y_train, X_test, y_test,
                             tarea, metrica, args.semilla)
    ganador, valor = sr.mejor_accion(juego, (), profundidad=2, turno_max=True)

    tabla = {}
    print("  peor caso por modelo:")
    for m in candidatos:
        vistos = {e: s for (mm, e), s in juego.resultados.items() if mm == m}
        if not vistos:
            continue
        peor = min(vistos, key=vistos.get)
        podado = len(vistos) < len(sr.ESCENARIOS)
        tabla[m] = {"peor_score": vistos[peor], "peor_escenario": peor,
                    "podado": podado, "scores": vistos}
        print(f"    {m:<14} {'<=' if podado else '= '} {vistos[peor]:.4f}  ({peor}"
              f"{', poda' if podado else ''})")
    total = len(candidatos) * len(sr.ESCENARIOS)
    print(f"  modelo mas robusto: {ganador} (garantiza {valor:.4f} {metrica})")
    print(f"  entrenamientos: {len(juego.resultados)} de {total} "
          f"(la poda ahorro {total - len(juego.resultados)}) | {time.time() - t0:.1f} s")
    return {"ganador": ganador, "score_garantizado": valor, "test_fold": int(test_fold),
            "modelos": tabla, "entrenamientos": len(juego.resultados), "total_sin_poda": total,
            "segundos": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--objetivo", choices=list(OBJETIVOS), default="is_active")
    ap.add_argument("--fuente", choices=("auto", "databricks", "local"), default="auto")
    ap.add_argument("--pasos", nargs="+", type=int, choices=(1, 2, 3), default=[1, 2, 3])
    ap.add_argument("--ia-generica", help="ruta a ia_generica (o variable IA_GENERICA)")
    ap.add_argument("--tolerancia", type=float, default=0.02,
                    help="paso 1: score que se acepta perder frente a usar todas las columnas")
    ap.add_argument("--max-variables", type=int, default=4,
                    help="paso 1: tamano maximo del subconjunto (acota la combinatoria)")
    ap.add_argument("--modelo-busqueda", default="arbol",
                    help="paso 1: modelo con que se puntua cada subconjunto")
    ap.add_argument("--poblacion", type=int, default=12, help="paso 2")
    ap.add_argument("--generaciones", type=int, default=10, help="paso 2")
    ap.add_argument("--modelos", nargs="+", default=["bosque", "arbol", "knn", "lineal", "mlp"],
                    help="paso 3: candidatos (primero el que esperas mejor: poda mas)")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--anidada", action="store_true",
                    help="validacion cruzada anidada: score sin sesgo de seleccion del genetico")
    ap.add_argument("--anidar-seleccion", action="store_true",
                    help="con --anidada, repite tambien el IDA* en cada fold (lento: ~3 min/fold)")
    ap.add_argument("--salida", default=str(SALIDA))
    args = ap.parse_args()

    mod = importar_ia_generica(args.ia_generica)
    datos = mod[0]
    tarea = OBJETIVOS[args.objetivo]
    metrica = "f1_macro" if tarea == "clasificacion" else "r2"

    df, origen = cargar_tabla(args.fuente)
    X, y, grupos, folds, excluidas = preparar(df, args.objetivo)

    print(f"Fuente: {origen}")
    print(f"Tabla: {len(df)} filas, {grupos.nunique()} enzimas, "
          f"{X.shape[1]} variables. Objetivo: {args.objetivo} ({tarea}). Metrica: {metrica}")
    print(f"Excluidas de X: {excluidas}")
    print(f"Particion fija '{FOLDS}': {folds.nunique()} folds "
          f"{folds.value_counts().sort_index().to_dict()}")
    if tarea == "clasificacion":
        vc = y.value_counts().sort_index()
        print(f"Balance de clases: {vc.to_dict()} "
              f"(la clase mayoritaria sola da accuracy {vc.max() / vc.sum():.1%})")

    # --- Baselines, en las MISMAS condiciones que todo lo demas ---------------
    print("\n=== Baselines (misma particion, misma metrica) ===")
    tonto = DummyClassifier(strategy="most_frequent") if tarea == "clasificacion" else \
        DummyRegressor(strategy="mean")
    base = {"trivial": evaluar_cv(datos.pipeline(tonto, X), X, y, folds, metrica),
            "bosque_todas_las_columnas": evaluar_cv(
                datos.pipeline(datos.construir_modelo("bosque", tarea, args.semilla), X),
                X, y, folds, metrica)}
    for n, r in base.items():
        print(linea(n, r, metrica))

    resultados = {"origen": origen, "objetivo": args.objetivo, "tarea": tarea,
                  "metrica": metrica, "filas": len(df), "enzimas": int(grupos.nunique()),
                  "variables": X.shape[1], "excluidas": excluidas,
                  "semilla": args.semilla, "baselines": base}

    if 1 in args.pasos:
        resultados["paso1_ida_estrella"] = paso1(mod, X, y, tarea, folds, grupos, metrica, args)
        elegidas = resultados["paso1_ida_estrella"].get("elegidas")
        if elegidas:
            r = evaluar_cv(datos.pipeline(datos.construir_modelo("bosque", tarea, args.semilla),
                                          X[elegidas]), X[elegidas], y, folds, metrica)
            resultados["paso1_ida_estrella"]["bosque_sobre_elegidas"] = r
            print(linea("bosque sobre las elegidas", r, metrica))

    if 2 in args.pasos:
        resultados["paso2_mlp_genetico"] = paso2(mod, X, y, tarea, folds, grupos, metrica, args)

    if 3 in args.pasos:
        cfg = resultados.get("paso2_mlp_genetico", {}).get("mejor_individuo")
        resultados["paso3_alfa_beta"] = paso3(mod, X, y, tarea, folds, grupos, metrica, args, cfg)

    if args.anidada:
        resultados["validacion_anidada"] = anidada(mod, X, y, tarea, folds, grupos, metrica, args)

    # --- Resumen comparable --------------------------------------------------
    print(f"\n=== Resumen ({metrica}, media +/- desv sobre los {folds.nunique()} folds de "
          f"'{FOLDS}') ===")
    filas = [("baseline trivial", base["trivial"]),
             ("baseline bosque (todas)", base["bosque_todas_las_columnas"])]
    p1 = resultados.get("paso1_ida_estrella", {})
    if p1.get("bosque_sobre_elegidas"):
        filas.append((f"paso 1: bosque sobre {len(p1['elegidas'])} columnas",
                      p1["bosque_sobre_elegidas"]))
    if "paso2_mlp_genetico" in resultados:
        filas.append(("paso 2: mlp_genetico", resultados["paso2_mlp_genetico"]["cv"]))
    for n, r in filas:
        print(linea(n, r, metrica))
    if "paso3_alfa_beta" in resultados:
        p3 = resultados["paso3_alfa_beta"]
        print(f"  {'paso 3: mas robusto':<28} {p3['ganador']} garantiza "
              f"{p3['score_garantizado']:.4f} en el peor escenario "
              f"(test = fold {p3['test_fold']})")
    if "validacion_anidada" in resultados:
        va = resultados["validacion_anidada"]
        print(f"\n  Anidada (cada fold se mide sin haber intervenido en ninguna eleccion):")
        for k, nombre in (("bosque", "bosque (sin ajuste)"), ("mlp_genetico", "mlp_genetico"),
                          ("bosque_seleccion", "bosque + IDA*")):
            if k in va:
                print(f"  {nombre:<28} {metrica} = {va[k]['media']:.4f} +/- {va[k]['desv']:.4f}")
        d = va["mlp_menos_bosque"]
        print(f"  {'mlp_genetico - bosque':<28} {d['media']:+.4f} +/- {d['desv']:.4f} "
              f"(gana mlp en {d['folds_gana_mlp']} de {d['folds']} folds)")
        if "frecuencia_columnas" in va:
            print(f"  columnas elegidas por IDA* (en cuantos folds): {va['frecuencia_columnas']}")
            if va.get("folds_sin_subconjunto"):
                print(f"  folds donde ningun subconjunto alcanzo el umbral: "
                      f"{va['folds_sin_subconjunto']} (no cuentan en la frecuencia)")

    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    destino = out / f"pipeline_{args.objetivo}.json"
    destino.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
    print(f"\nResultados en: {destino}")
    print("Pendiente: registrar la corrida en MLflow (algorithms/README.md).")


if __name__ == "__main__":
    main()
