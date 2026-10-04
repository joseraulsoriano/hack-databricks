"""Puerta de procedencia: decide si una hipotesis entra al laboratorio.

La admision NO es un juicio de nadie, ni humano ni de un modelo: es una funcion
determinista de los datos ya curados. El mismo input sobre el mismo corpus da el
mismo veredicto, y un tercero lo puede recalcular y obtener el mismo recibo.

Lo que esta puerta PRUEBA, en el sentido en que 2+2 es comprobable:

  - que cada `doc_id` citado existe en el corpus curado y lo aprobo una persona;
  - que cada `evidence_span` aparece LITERAL en el documento al que se le atribuye
    (cotejo de cadena, no parecido semantico);
  - que cada valor numerico afirmado aparece en su propia frase de evidencia;
  - que todo registro numerico citado esta marcado `verified = true`;
  - que las variables de las que habla la hipotesis existen como columnas reales.

Lo que esta puerta NO prueba, y conviene decirlo antes de que lo pregunten: que la
hipotesis sea CIERTA. Una hipotesis admitida esta *fundada en datos reales y
trazables*; sigue siendo una hipotesis. La puerta separa "esto viene de la
literatura publicada y se puede rastrear" de "esto se lo invento el extractor".

Sin red y sin Databricks: recibe el corpus, los registros y las columnas como
datos. Quien llame decide si vienen de Unity Catalog o de un archivo local.
"""

import hashlib
import json
import re
import unicodedata

# Normalizacion declarada y minima. Cuanto menos toque, mas comprobable a mano es
# el cotejo: la idea es que una persona pueda leer la frase en el PDF y verla.
GUIONES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
ESPACIOS = re.compile(r"\s+")

# Un fragmento corto casa con cualquier cosa. Exigimos una frase, no tres palabras.
SPAN_MINIMO = 40
# Una sola fuente no es diversidad. Avisa, no rechaza: hay datos que solo publico un grupo.
FUENTES_MINIMAS = 2

VEREDICTOS = ("ADMITIDA", "ADMITIDA_CON_AVISOS", "RECHAZADA")


def normalizar(texto: str) -> str:
    """NFKC, guiones Unicode unificados, espacios colapsados, sin distinguir mayusculas.

    El guion es el caso real del corpus: `LCC-ICCG` aparece con U+2010 en unos
    articulos y con el guion ASCII en otros (ver docs/EXTRACCION_MUTANTES.md).
    """
    t = unicodedata.normalize("NFKC", texto or "").translate(GUIONES)
    return ESPACIOS.sub(" ", t).strip().casefold()


def representaciones(valor: float) -> list[str]:
    """Formas en que un mismo numero puede estar escrito en una frase publicada."""
    formas = {f"{valor:g}", f"{valor:.1f}", f"{valor:.2f}"}
    if float(valor).is_integer():
        formas.add(str(int(valor)))
    formas |= {f.replace(".", ",") for f in list(formas)}  # 85,8 en textos europeos
    return sorted(formas)


def valor_aparece(valor: float, texto: str) -> bool:
    """True si el numero esta escrito en el texto, no si se parece a otro que esta.

    Con frontera de digitos a los dos lados, incluido el separador decimal: en una
    frase que dice 85.8, ni 85 ni 8 cuentan como valor afirmado.
    """
    t = normalizar(texto)
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d]|[.,]\d)", t)
               for f in representaciones(valor))


def _check(nombre, pasa, valor, umbral, detalle="", fatal=True) -> dict:
    return {"name": nombre, "passed": bool(pasa), "value": float(valor),
            "threshold": float(umbral), "detail": detalle, "fatal": bool(fatal)}


def _donde_aparece(span: str, corpus: dict) -> list[str]:
    """Documentos del corpus cuyo texto contiene el span. Delata una mala atribucion."""
    aguja = normalizar(span)
    return sorted(d for d, doc in corpus.items() if aguja and aguja in normalizar(doc.get("text", "")))


def verificar(hipotesis: dict, corpus: dict, registros: dict | None = None,
              columnas=()) -> dict:
    """Pasa una hipotesis por la puerta y devuelve su recibo.

    hipotesis: statement, prediction, variables[], respaldo[], submitted_by.
       Cada respaldo: doc_id, evidence_span y, si afirma un numero, value y
       opcionalmente record_id de `mutant_stability`.
    corpus:    doc_id -> {"text", "approved_by", "source"} (ya curado y aprobado).
    registros: record_id -> {"value", "unit", "verified", "doc_id", "evidence_span"}.
    columnas:  nombres de columna existentes (p. ej. de `pet_activity_ml`).

    Devuelve: veredicto, checks (cada uno con valor y umbral), avisos y un hash
    reproducible del recibo.
    """
    registros = registros or {}
    columnas = set(columnas)
    respaldo = hipotesis.get("respaldo") or []
    checks: list[dict] = []

    # 1. Una hipotesis sin respaldo es una pregunta, y las preguntas no entran por
    #    esta puerta. Este es el check que sostiene "entradas curadas, no pivotables".
    checks.append(_check("respaldo_presente", len(respaldo) >= 1, len(respaldo), 1,
                         "una hipotesis sin evidencia citada es una pregunta, no una hipotesis"))

    # 2. Tiene que afirmar algo medible, o no hay nada que contrastar despues.
    enunciado = (hipotesis.get("statement") or "").strip()
    prediccion = (hipotesis.get("prediction") or "").strip()
    checks.append(_check("enunciado_presente", len(enunciado) >= 20, len(enunciado), 20))
    checks.append(_check("prediccion_presente", len(prediccion) >= 20, len(prediccion), 20,
                         "sin prediccion medible no hay experimento que la pueda contradecir"))

    # 3. Las variables de las que habla tienen que existir de verdad.
    variables = list(hipotesis.get("variables") or [])
    if columnas:
        faltan = [v for v in variables if v not in columnas]
        checks.append(_check("variables_existen", not faltan, len(variables) - len(faltan),
                             len(variables), f"no existen como columna: {', '.join(faltan)}" if faltan else ""))

    # 4. El nucleo: cada pieza de respaldo, una por una.
    fuentes = set()
    for i, r in enumerate(respaldo):
        pre = f"respaldo[{i}]"
        doc_id = (r.get("doc_id") or "").strip()
        span = r.get("evidence_span") or ""
        doc = corpus.get(doc_id)

        checks.append(_check(f"{pre}.doc_existe", doc is not None, 1 if doc else 0, 1,
                             f"{doc_id or '(vacio)'} no esta en el corpus curado"))
        if doc is None:
            continue
        fuentes.add(doc.get("source") or "")

        # Aprobacion humana: el agente escribe en staging, la persona promueve a curated.
        aprobado = bool((doc.get("approved_by") or "").strip())
        checks.append(_check(f"{pre}.doc_aprobado", aprobado, 1 if aprobado else 0, 1,
                             f"{doc_id} no tiene approved_by: nadie lo paso a curated"))

        checks.append(_check(f"{pre}.span_suficiente", len(span.strip()) >= SPAN_MINIMO,
                             len(span.strip()), SPAN_MINIMO,
                             "un fragmento corto casa con demasiadas frases"))

        # El cotejo literal. Esto es lo mas cercano a 2+2 que hay en todo el sistema.
        literal = normalizar(span) in normalizar(doc.get("text", "")) if span.strip() else False
        detalle = ""
        if not literal and span.strip():
            otros = [d for d in _donde_aparece(span, corpus) if d != doc_id]
            detalle = (f"la frase no esta en {doc_id}; si aparece en: {', '.join(otros)}"
                       if otros else f"la frase no aparece literal en {doc_id}")
        checks.append(_check(f"{pre}.span_literal", literal, 1 if literal else 0, 1, detalle))

        # Si afirma un numero, el numero tiene que estar en su propia frase.
        if r.get("value") is not None:
            valor = float(r["value"])
            en_span = valor_aparece(valor, span)
            checks.append(_check(f"{pre}.valor_en_span", en_span, 1 if en_span else 0, 1,
                                 f"{valor} no esta escrito en la frase que se cita como evidencia"))

        # Si cita una fila de mutant_stability, esa fila tiene que estar verificada.
        record_id = (r.get("record_id") or "").strip()
        if record_id:
            fila = registros.get(record_id)
            checks.append(_check(f"{pre}.registro_existe", fila is not None, 1 if fila else 0, 1,
                                 f"{record_id} no esta en mutant_stability"))
            if fila is not None:
                ver = bool(fila.get("verified"))
                checks.append(_check(f"{pre}.registro_verificado", ver, 1 if ver else 0, 1,
                                     f"{record_id} sigue en verified=false: lo extrajo "
                                     f"{fila.get('extracted_by', 'un agente')} y nadie lo reviso"))
                if r.get("value") is not None and fila.get("value") is not None:
                    coincide = abs(float(fila["value"]) - float(r["value"])) < 1e-6
                    checks.append(_check(f"{pre}.valor_coincide_registro", coincide,
                                         float(fila["value"]), float(r["value"]),
                                         "" if coincide else "el valor afirmado no es el de la fila citada"))

    # 5. Diversidad de fuentes: aviso, nunca rechazo.
    utiles = {f for f in fuentes if f}
    checks.append(_check("fuentes_distintas", len(utiles) >= FUENTES_MINIMAS, len(utiles),
                         FUENTES_MINIMAS, f"toda la evidencia viene de: {', '.join(sorted(utiles)) or 'ninguna'}",
                         fatal=False))

    fallos = [c["name"] for c in checks if not c["passed"] and c["fatal"]]
    avisos = [f"{c['name']}: {c['detail']}" for c in checks if not c["passed"] and not c["fatal"]]
    veredicto = "RECHAZADA" if fallos else ("ADMITIDA_CON_AVISOS" if avisos else "ADMITIDA")

    recibo = {"veredicto": veredicto, "checks": checks, "fallos": fallos, "avisos": avisos,
              "hipotesis": hipotesis}
    recibo["recibo_hash"] = hashlib.sha256(
        json.dumps(recibo, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
    return recibo


def explicar(recibo: dict) -> str:
    """El recibo en texto, para la consola o para pegarlo en el informe."""
    lineas = [f"{recibo['veredicto']}  (recibo {recibo['recibo_hash']})"]
    for c in recibo["checks"]:
        marca = "ok  " if c["passed"] else ("FALLA" if c["fatal"] else "aviso")
        lineas.append(f"  [{marca}] {c['name']}: {c['value']:g} / {c['threshold']:g}"
                      + (f" — {c['detail']}" if c["detail"] and not c["passed"] else ""))
    return "\n".join(lineas)
