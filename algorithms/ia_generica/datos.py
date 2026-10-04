"""
Utilidades comunes para procesar un dataset tabular (CSV).

Las usan los tres procesos:
  seleccion_variables.py  (IDA*)
  seleccion_robusta.py    (poda alfa-beta)
  mlp_genetico.py         (red neuronal + algoritmo genetico)
"""
import pandas as pd
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import (GroupKFold, GroupShuffleSplit, KFold, PredefinedSplit,
                                     StratifiedGroupKFold, StratifiedKFold, train_test_split)
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

CLASIFICACION = "clasificacion"
REGRESION = "regresion"
METRICA_DEFECTO = {CLASIFICACION: "f1_macro", REGRESION: "r2"}


def cargar(csv, objetivo, sep=",", excluir=(), grupos=None):
    """Lee el CSV y separa variables (X) de la columna a predecir (y).

    Devuelve (X, y, g). 'g' es None salvo que se pida 'grupos'.

    excluir: columnas que NO son variables y hay que sacar de X: identificadores,
             otros objetivos (si predices 'is_active', 'activity' es una fuga de
             informacion) y metadatos de la particion.
    grupos:  columna que identifica la unidad de medida que no se puede partir
             (p. ej. la enzima, medida en varias condiciones). Se devuelve aparte
             y se saca de X.
    """
    df = pd.read_csv(csv, sep=sep)
    faltan = [c for c in [objetivo, *excluir, *([grupos] if grupos else [])] if c not in df.columns]
    if faltan:
        raise ValueError(f"Estas columnas no existen: {faltan}. Columnas: {list(df.columns)}")
    df = df.dropna(subset=[objetivo])
    g = df[grupos] if grupos else None
    fuera = [objetivo, *excluir, *([grupos] if grupos else [])]
    return df.drop(columns=list(dict.fromkeys(fuera))), df[objetivo], g


def detectar_tarea(y) -> str:
    """Texto, booleano o pocos valores enteros -> clasificacion; continuos -> regresion.

    Un objetivo 0/1 con celdas vacias lo lee pandas como float (usa NaN para los
    huecos), asi que los decimales que en realidad son enteros tambien cuentan
    como clasificacion. Si el objetivo es ambiguo, pasa --tarea a mano.
    """
    if not pd.api.types.is_numeric_dtype(y) or pd.api.types.is_bool_dtype(y):
        return CLASIFICACION
    sin_nulos = y.dropna()
    if y.nunique() <= 20 and len(sin_nulos) and (sin_nulos % 1 == 0).all():
        return CLASIFICACION
    return REGRESION


def indices_division(X, y, tarea, test=0.25, semilla=42, grupos=None):
    """Indices (train, test) de la particion. Con 'grupos', ninguna unidad cae
    en train y test a la vez. Usalo cuando tambien necesites los grupos del
    train para la validacion cruzada."""
    if grupos is not None:
        return next(GroupShuffleSplit(n_splits=1, test_size=test,
                                      random_state=semilla).split(X, y, grupos))
    estratos = y if tarea == CLASIFICACION else None
    return train_test_split(range(len(X)), test_size=test, random_state=semilla,
                            stratify=estratos)


def dividir(X, y, tarea, test=0.25, semilla=42, grupos=None):
    """Particion train/test: X_train, X_test, y_train, y_test.

    Estratificada en clasificacion; por grupo si se pasan 'grupos'."""
    tr, te = indices_division(X, y, tarea, test, semilla, grupos)
    return X.iloc[tr], X.iloc[te], y.iloc[tr], y.iloc[te]


def particion_cv(tarea, k=3, semilla=42, grupos=None, cv_split=None):
    """Objeto de validacion cruzada.

    cv_split: fold ya asignado a cada fila (particion fija publicada con los
              datos). Manda sobre 'k' y sobre 'grupos'.
    grupos:   si se pasan, ninguna unidad cae a la vez en train y validacion.
              Hay que pasar 'groups=' tambien a cross_val_score.
    """
    if cv_split is not None:
        return PredefinedSplit(cv_split)
    if grupos is not None:
        Folds = StratifiedGroupKFold if tarea == CLASIFICACION else GroupKFold
        return Folds(n_splits=k, shuffle=True, random_state=semilla)
    Folds = StratifiedKFold if tarea == CLASIFICACION else KFold
    return Folds(n_splits=k, shuffle=True, random_state=semilla)


def construir_preprocesador(X: pd.DataFrame) -> ColumnTransformer:
    """Numericas -> imputacion + escalado. Texto -> imputacion + One-Hot."""
    numericas = X.select_dtypes(include="number").columns.tolist()
    categoricas = [c for c in X.columns if c not in numericas]
    return ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numericas),
        ("cat", make_pipeline(SimpleImputer(strategy="most_frequent"),
                              OneHotEncoder(handle_unknown="ignore")), categoricas),
    ])


MODELOS = ["arbol", "bosque", "lineal", "knn", "mlp"]


def construir_modelo(nombre: str, tarea: str, semilla=42, **params):
    """Modelo de sklearn por nombre ('arbol', 'bosque', 'lineal', 'knn', 'mlp')."""
    clf = tarea == CLASIFICACION
    if nombre == "arbol":
        m = (DecisionTreeClassifier if clf else DecisionTreeRegressor)(
            max_depth=params.pop("max_depth", 8), random_state=semilla, **params)
    elif nombre == "bosque":
        m = (RandomForestClassifier if clf else RandomForestRegressor)(
            n_estimators=params.pop("n_estimators", 100), random_state=semilla, n_jobs=-1, **params)
    elif nombre == "lineal":
        m = LogisticRegression(max_iter=1000, **params) if clf else Ridge(**params)
    elif nombre == "knn":
        m = (KNeighborsClassifier if clf else KNeighborsRegressor)(**params)
    elif nombre == "mlp":
        params.setdefault("early_stopping", True)
        m = (MLPClassifier if clf else MLPRegressor)(random_state=semilla, **params)
    else:
        raise ValueError(f"Modelo desconocido '{nombre}'. Opciones: {MODELOS}")
    if not clf and nombre in ("lineal", "knn", "mlp"):
        # Objetivos de escala grande (precios, montos...) se estandarizan.
        m = TransformedTargetRegressor(regressor=m, transformer=StandardScaler())
    return m


def pipeline(modelo, X: pd.DataFrame) -> Pipeline:
    """Preprocesamiento + modelo, para que el escalado se ajuste solo con train."""
    return Pipeline([("prep", construir_preprocesador(X)), ("modelo", modelo)])
