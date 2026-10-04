"""Extrae workspace.lab.mutant_stability de los chunks curados, con cita obligatoria.

Paso D de docs/CURACION.md. Cada fila sale de UNA oracion, que se guarda entera en
evidence_span y SIEMPRE contiene el valor extraido: si no lo contiene, no hay fila.
Por defecto NO escribe en Databricks; deja una propuesta para revision humana
(verified queda en false y extracted_by = agent:regex_extractor).

COMO EXTRAE
Reglas deterministicas y auditables, no un modelo. Trabaja por oracion:

  1. Busca la etiqueta de Tm (Tm, T m, melting temperature) seguida de un valor
     en grados C. Sin valor no hay fila (asi "(TM)" = "triple mutant" no entra).
  2. Atribuye la fila a la enzima mas CERCANA por delante del Tm, no a la primera
     de la ventana. Las mutaciones son las que aparecen entre la enzima y el Tm.
  3. Distingue valor absoluto (Tm) de incremento (dTm): lo marca un simbolo delta,
     un "by", o un verbo de cambio (increase/decrease) con "of". "to" es absoluto.
  4. "Tm values of A and B were X and Y" emite dos filas, emparejadas por orden.

QUE RECHAZA (errores que un extractor ingenuo comete, vistos en este corpus)
  - "Δ T m = 24.9 C" como Tm: es un incremento y se guarda como dTm.
  - "(TM)" cuando significa triple mutant.
  - La temperatura de fusion del PLASTICO ("PET samples ... melting temperature").
  - Tm fuera de 20-120 C, o dos numeros en rango sin ser incremento.
  - Tablas aplanadas: no se parsean a ciegas, se listan en tablas_pendientes.json
    para extraccion asistida o manual.

CONFIANZA (para ordenar la revision, NO para saltarsela)
  alta   enzima concreta, unica en la oracion, con mutaciones o wild-type explicito
  media  enzima generica ("PETase") o varias enzimas en la misma oracion
  baja   sin enzima, valor aproximado/en rango, o incremento por verbo

Uso:
    uv run python -m data_pipeline.curation.extraer_mutantes
    uv run python -m data_pipeline.curation.extraer_mutantes --revisar 20 --confianza baja
    uv run python -m data_pipeline.curation.extraer_mutantes --aprobar "Tu Nombre"
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHUNKS = ROOT / "data" / "resultados" / "curacion" / "chunks.jsonl"
SALIDA = ROOT / "data" / "resultados" / "curacion"
DESTINO = "workspace.lab.mutant_stability"
EXTRACTOR = "agent:regex_extractor"

AA = "ACDEFGHIKLMNPQRSTVWY"
TM_MIN, TM_MAX = 20.0, 120.0       # rango fisico plausible para el Tm de una proteina
DTM_MAX = 60.0

MUTACION = re.compile(rf"(?<![A-Za-z0-9])[{AA}]\d{{1,4}}[{AA}](?![A-Za-z0-9])")
TRES = {"Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E",
        "Gly": "G", "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F",
        "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V"}
_T = "|".join(TRES)
MUT3 = re.compile(rf"(?<![A-Za-z])({_T})(\d{{1,4}})({_T})(?![A-Za-z])")
MUT_ANY = re.compile(MUTACION.pattern + "|" + MUT3.pattern)
# Nombre de enzima, con prefijo de especie ("Is PETase", "Sd PETase") y el sufijo de
# variante que lo acompana ("LCC-ICCG-NM", "IsPETase-Cat"): sin el sufijo una variante
# se confunde con la enzima base.
_SUF = r"(?:-(?!(?:like|based|degrading|catalyzed|mediated|producing|expressing|derived|specific|related|type|family)\b)[A-Za-z0-9]{1,8})*"
ENZIMA = re.compile(
    r"(?<![A-Za-z])(?:"
    r"[A-Z][A-Za-z]{1,9}-?PETase"
    r"|(?!(?:The|Our|Its|This|That|Any|Not|And|For|One)\b)[A-Z][a-z]{1,2}\s+PETase\d{0,2}"
    r"|PETase|LCC(?:-ICCG)?|ICCG|Tf\s?Cut\s?2|TfH|Cut190\*?|(?:[A-Z][a-z]\s?)?MHETase|HiC"
    r"|(?:[A-Z][a-z]{2,4}\s?_)?PES-?H1|(?:[A-Z][a-z]{2,4}\s?_)?PE-?H|(?i:cutinase|esterase|lipase))"
    + _SUF + r"(?![A-Za-z])")
GENERICAS = {"petase", "cutinase", "esterase", "lipase"}
WT = re.compile(r"(?i)\b(WT|W/T|wild[- ]?type|native)\b")
CANON = {"ispetase": "IsPETase", "fast-petase": "FAST-PETase", "thermopetase": "ThermoPETase",
         "durapetase": "DuraPETase", "hotpetase": "HotPETase", "tfcut2": "TfCut2",
         "lcc": "LCC", "lcc-iccg": "LCC-ICCG", "pes-h1": "PES-H1", "pe-h": "PE-H",
         "cut190*": "Cut190*", "petase": "PETase"}

TOKEN = re.compile(
    r"(?P<d>[Δ∆]\s*|\bdelta\s+)?"
    r"(?:(?<![A-Za-z])T\s?m(?:\s?app)?\b|melting temperature|thermal denaturation temperature)",
    re.I)
VALOR = re.compile(
    r"\s*(?:values?\s*)?"
    r"(?P<vb>(?:increas|decreas|improv|rais|reduc|elevat)\w*\s+)?"
    r"(?:(?:of|for)\s+(?P<ent2>(?:(?![Tt]\s?m\b|increas|decreas|improv|reduc)[^=:]){1,60}?)\s+(?P<cierre>was|is|=|:|to|by)\s*"
    r"|(?P<conn>of|at|by|to|was|is|=|:)\s*"
    r"|than\s+[^0-9]{1,40}?\s+(?P<por>by)\s+)?"
    r"(?:(?:measured|found|determined|estimated|calculated|observed)\s+(?:to\s+be|as|at)\s+"
    r"|reached\s+|to\s+be\s+|(?:increas|improv|rais|elevat)\w*\s+to\s+)?"
    r"(?P<upto>up\s+to\s+)?"
    r"(?P<aprox>(?:about|approximately|around|~|ca\.)\s*)?"
    r"(?P<sg>[+\-−–])?\s*(?P<v>\d{1,3}(?:\.\d+)?)"
    r"(?:\s*[-–]\s*(?P<v2>\d{1,3}(?:\.\d+)?))?\s*(?:°|º)?\s*C\b", re.I)
PAREJA = re.compile(
    r"(?:of|for)\s+(?P<a>[^,;]{3,70}?)\s+and\s+(?P<b>[^,;]{3,70}?)\s+(?:were|was|are)\s+"
    r"(?P<x>\d{1,3}(?:\.\d+)?)\s+and\s+(?P<y>\d{1,3}(?:\.\d+)?)\s*(?:°|º)?\s*C\b", re.I)
DIRECCION = re.compile(r"(?i)(increas|improv|rais|elevat|decreas|drop|reduc|lower)\w*")
NEGATIVAS = re.compile(r"(?i)^(decreas|drop|reduc|lower)")
POLIMERO = re.compile(r"(?i)\b(PET|polymers?|plastics?|films?|coupons?|samples?|PCL|PBS|PBSA|PBAT|"
                      r"PBSF\d*|PLA|PHB|PHA|PEF|polyesters?|polycaprolactone|polyurethane|"
                      r"semi-?crystalline)\b")
# Propiedades de un POLIMERO junto al valor ("T g of -43 C and T m of 89 C", "modulus of
# 393 MPa"). Se mira solo la ventana alrededor del valor: en frases sobre enzimas tambien
# aparecen "PET T g" o "low-crystallinity PET" sin que el Tm sea del plastico.
ES_POLIMERO = re.compile(r"(?i)\bT\s?g\b\s*(?:of|=|around|near|:)?\s*[\u2212\-\u2013]?\s*\d"
                         r"|\b(modulus|elongation\s+at\s+break|tensile|mol\s?%)\b")
# El Tm cambia con el medio: sin esto un valor medido con 20% de MeCN o con Ca2+ parece
# la Tm "normal" de la enzima.
CONDICION = re.compile(
    r"(?i)\b(in\s+the\s+(?:presence|absence)\s+of\s+[^,.;()]{1,30}|upon\s+(?:increasing|adding)\s+"
    r"[^,.;()]{1,30}|without\s+the\s+addition\s+of\s+[^,.;()]{1,25}|\d+(?:\.\d+)?\s?%\s*\(v/v\)|"
    r"\d+(?:\.\d+)?\s?M\s+NaCl|MeCN|glycerol|ionic\s+strength|buffer\s+concentration)")
RANGO = re.compile(r"(?i)\brang(?:e|ed|es|ing)\b")
ABREV = re.compile(r"\b(Fig|Figs|Supp|Suppl|al|vs|approx|ca|Ref|Refs|No|Eq)\.\s")


def oraciones(texto: str) -> list[str]:
    """Parte en oraciones sin cortar 'Fig. 4' ni 'et al. 2010'."""
    protegido = ABREV.sub(lambda m: m.group(0)[:-1] + "\x00", texto)
    partes = re.split(r"(?<=\.)\s+(?=[A-Z(\[])|;\s+|\n+", protegido)
    return [p.replace("\x00", " ").strip() for p in partes if p.strip()]


def canon(nombre: str) -> str:
    plano = re.sub(r"\s+", "", nombre)
    return CANON.get(plano.lower(), plano)


COMPARADOR = re.compile(
    r"(?i)(?:compared\s+(?:to|with)|relative\s+to|versus|vs\.?|than|against|"
    r"(?:similar|comparable|homologous|identical|identity|similarity)\w*(?:\s+\S+){0,10}?\s+(?:to|with)|"
    r"(?:superior|inferior)\s+to|with\s+respect\s+to|(?:in\s+)?addition\s+to|added\s+to|"
    r"introduc\w*\s+(?:in|into)|applied\s+to|fused\s+to|on\s+top\s+of|based\s+on|"
    r"derived\s+from|engineered\s+from|redesign\w*\s+(?:of|from))\s+(?:that\s+of\s+|the\s+)?"
    r"(?:(?:wild[- ]?type|WT|native|parent(?:al)?)\s+)?$")
CLAUSULA = re.compile(r"(?i)\b(and|while|whereas|but|which|than|compared|although|however|despite)\b")


def muts_en(texto: str) -> list[str]:
    """Mutaciones en notacion de una letra; acepta tambien Ser121Glu."""
    out = []
    for m in MUT_ANY.finditer(texto):
        s = m.group(0)
        if MUTACION.fullmatch(s):
            out.append(s)
        else:
            g = MUT3.fullmatch(s)
            out.append(TRES[g.group(1)] + g.group(2) + TRES[g.group(3)])
    return list(dict.fromkeys(out))


def _variante(seg: str) -> str:
    """Designador de variante que sigue a la enzima: 'Ca PETase M9', 'variant, M2'.
    Sin el, una variante se confunde con la enzima base."""
    m = re.match(r"-\s+([A-Z][a-z]{1,2})(?![A-Za-z0-9])", seg)          # "Is PETase- Pp"
    if m:
        return m.group(1)
    m = re.match(r"\s*(?:variant\s+|mutant\s+)?([A-Z][A-Za-z]?\d{1,3})(?![A-Za-z0-9])", seg)
    if m:
        return m.group(1)
    m = re.search(r"(?i:variants?|mutants?)[,:]?\s+(?:(?i:named|designated)\s+(?:as\s+)?)?"
                  r"([A-Z][A-Za-z]{0,3}\d{1,3})(?![A-Za-z0-9])", seg)
    return m.group(1) if m else ""


def _nombre(c, seg: str, muts: str) -> tuple[str, str]:
    """(enzima, nota). El designador de variante pasa a formar parte del nombre."""
    base = canon(c.group(0))
    tag = _variante(seg)
    if tag:
        return f"{base} {tag}", ""
    return base, "wild-type" if (not muts and WT.search(seg)) else ""


def _mismo_sujeto(segmento: str) -> bool:
    """True si entre la enzima y el Tm no hay un limite de clausula (and, while, ...).

    Las mutaciones enumeradas ("S121E and D186H") no cuentan como limite."""
    plano = MUT_ANY.sub("§", segmento)
    plano = re.sub(r"§(?:\s*(?:and|,|/|\+)\s*§)+", "§", plano)
    return not CLAUSULA.search(plano)


def _atribuir(oracion: str, fin: int, ent_texto: str | None = None) -> tuple[str, str, str, bool]:
    """Devuelve (enzima, mutaciones, nota, directa).

    'directa' = la enzima es el sujeto del Tm: sin comparador delante, sin otro valor
    ya consumido y sin limite de clausula entre ambos. Solo asi la fila puede ser alta."""
    if ent_texto:
        cand = list(ENZIMA.finditer(ent_texto))
        if not cand:
            return "", ",".join(muts_en(ent_texto)), "", False
        c = cand[-1]
        seg = ent_texto[c.end():][:160]
        muts = ",".join(muts_en(seg))
        nombre, nota = _nombre(c, seg, muts)
        return nombre, muts, nota, True

    ambito = oracion[:fin]
    cand = list(ENZIMA.finditer(ambito))
    while cand:
        previo = ambito[max(0, cand[-1].start() - 45):cand[-1].start()]
        if COMPARADOR.search(previo):          # "compared to wild-type Is PETase"
            cand.pop()
            continue
        if re.search(r"\d\s*[°º]\s*C", ambito[cand[-1].end():]):
            cand = []                          # esa enzima ya tiene su valor
        break
    if not cand:
        return "", ",".join(muts_en(ambito[-100:])), "", False
    c = cand[-1]
    seg = ambito[c.end():][:160]
    muts = ",".join(muts_en(seg))
    antes = ambito[max(0, c.start() - 25):c.start()]
    nombre, nota = _nombre(c, seg, muts)
    if not nota and not muts and not _variante(seg) and WT.search(antes):
        nota = "wild-type"
    return nombre, muts, nota, _mismo_sujeto(seg)


def _polimero_mas_cerca(oracion: str, pos: int) -> bool:
    """True si, antes del Tm, el PET/plastico esta mas cerca que cualquier enzima."""
    previo = oracion[max(0, pos - 90):pos]
    p = [m.end() for m in POLIMERO.finditer(previo)]
    e = [m.end() for m in ENZIMA.finditer(previo)]
    return bool(p) and (not e or max(p) > max(e))


def _fila(oracion, enzima, muts, nota, metrica, valor, conf, aprox, cond, patron, literal):
    return {"enzyme": enzima, "mutations": muts, "metric": metrica, "value": valor,
            "unit": "C", "conditions": "; ".join(x for x in (nota, cond) if x),
            "evidence_span": oracion[:2000], "_patron": patron, "_confianza": conf,
            "_aproximado": aprox, "_literal": literal}


def normalizar(texto: str) -> str:
    """Unifica guiones Unicode y 'LCC ICCG' -> 'LCC-ICCG' SIN cambiar la longitud."""
    return (texto.replace("\u2010", "-").replace("\u2011", "-")
            .replace("LCC ICCG", "LCC-ICCG"))


def filas_de_oracion(orig: str) -> list[dict]:
    """Analiza el texto normalizado; la evidencia guardada es la original."""
    filas = _filas(normalizar(orig))
    for f in filas:
        f["evidence_span"] = orig[:2000]
    return filas


def _filas(o: str) -> list[dict]:
    if not TOKEN.search(o):
        return []
    
    p = PAREJA.search(o)
    if p and TOKEN.search(o[:p.start() + 12]):
        filas = []
        for lado, num in (("a", p.group("x")), ("b", p.group("y"))):
            v = float(num)
            if not TM_MIN <= v <= TM_MAX:
                continue
            enz, muts, nota, _ = _atribuir(o, 0, p.group(lado))
            filas.append(_fila(o, enz, muts, nota, "Tm", v, "media", False, "", "pareja", num))
        return filas

    filas = []
    previo_fin = 0
    for t in TOKEN.finditer(o):
        if _polimero_mas_cerca(o, t.start()):
            continue
        m = VALOR.match(o, t.end())
        if not m:
            continue
        if ES_POLIMERO.search(o[max(0, t.start() - 70):m.end() + 70]):
            continue
        # "had a range of Tm of 21.66 C" / "ranged from 36.4 to 80.1 C": no es un Tm.
        if RANGO.search(o[max(0, t.start() - 40):m.end() + 10]):
            continue
        # "melting temperature was 57 C for MW 14 000 PCL": es del polimero.
        if re.match(r"\s*(?:,|\()?\s*for\b[^.]{0,30}", o[m.end():]) and \
                POLIMERO.search(o[m.end():m.end() + 45]):
            continue
        v = float(m.group("v"))
        v2 = float(m.group("v2")) if m.group("v2") else None
        aprox = bool(m.group("aprox")) or bool(m.group("upto"))
        ventana = o[max(previo_fin, t.start() - 50):t.start()]
        previo_fin = m.end()
        verbo = None if re.search(r"°\s*C|º\s*C", ventana) else DIRECCION.search(ventana)

        conn = (m.group("conn") or m.group("cierre") or "").lower()
        cmp_ = re.match(r"\s*(?P<c>higher|greater|warmer|lower|above|below)\b", o[m.end():], re.I)
        es_delta = bool(t.group("d")) or bool(m.group("por")) or conn == "by" or bool(cmp_) \
            or (bool(m.group("vb")) and conn == "of") \
            or (verbo is not None and conn in ("of", "") and not m.group("cierre") and not m.group("upto") and v <= 40)
        tras = re.match(r"\s*(?:,|\()?\s*for\s+(?:the\s+)?(?P<e>(?:(?!\band\b)[^,;()]){2,45})",
                        o[m.end():])
        if m.group("ent2"):
            enz, muts, nota, directa = _atribuir(o, 0, m.group("ent2"))
        elif tras and ENZIMA.search(tras.group("e")):
            # "Tm = 85.8 C for LCC and 93.3 C for LCC-ICCG": el valor es de la que sigue a 'for'.
            enz, muts, nota, directa = _atribuir(o, 0, tras.group("e"))
        elif t.group("d") and filas:
            # "X: S121E T m = 57.6 C, Δ T m = +8.8 C": el incremento es de la misma fila.
            enz, muts, nota, directa = filas[-1]["enzyme"], filas[-1]["mutations"], "", True
        else:
            enz, muts, nota, directa = _atribuir(o, t.start())

        etiqueta = ""
        if not enz:
            h = re.search(r"([A-Z][\w\-]{1,18})\s*\(\s*$", o[max(0, t.start() - 30):t.start()])
            if h:
                etiqueta = f"etiqueta en el texto: {h.group(1)}"

        if es_delta:
            if not (0 < v <= DTM_MAX and (v2 is None or 0 < v2 <= DTM_MAX)):
                continue
            medio = v if v2 is None else (v + v2) / 2
            if m.group("sg") in ("-", "−", "–"):
                signo = -1
            elif m.group("sg") == "+":
                signo = 1
            else:
                if m.group("vb"):
                    negativo = bool(NEGATIVAS.match(m.group("vb")))
                else:
                    negativo = (verbo and NEGATIVAS.match(verbo.group(0))) or \
                        (cmp_ and cmp_.group("c").lower() in ("lower", "below"))
                signo = -1 if negativo else 1
            if not enz and not muts:
                continue
            cond = f"rango {v:g}-{v2:g} C" if v2 is not None else ""
            if enz and not muts and len({canon(x.group(0)) for x in ENZIMA.finditer(o)}) == 1:
                # "introducing S121E and D186H in IsPETase ... increased Tm by 8.8":
                # las mutaciones van ANTES del nombre. Solo se acepta si son pocas.
                previas = muts_en(o)
                if 0 < len(previas) <= 4:
                    muts = ",".join(previas)
                    cond = "; ".join(x for x in (cond, "mutaciones tomadas de la oracion") if x)
            filas.append(_fila(o, enz, muts, nota, "dTm", round(signo * medio, 3), "baja",
                               aprox or v2 is not None, cond, "delta", m.group("v")))
            continue

        if v2 is not None or not TM_MIN <= v <= TM_MAX:
            continue
        concreta = bool(enz) and enz.lower() not in GENERICAS
        cond = ""
        if not enz or aprox:
            conf = "baja"
        elif (verbo is not None or m.group("vb")) and conn == "to":
            # "increase the Tm of X to 81.1 C by adding ...": el valor es de X MODIFICADA.
            conf, cond = "baja", "valor tras una modificacion; verificar de que variante es"
        elif not directa:
            conf, cond = "baja", "enzima no ligada con certeza a este valor"
        elif muts or nota or concreta:
            conf = "alta"
        else:
            conf = "media"
        cond = "; ".join(x for x in (cond, etiqueta) if x)
        filas.append(_fila(o, enz, muts, nota, "Tm", v, conf, aprox, cond, "valor", m.group("v")))

    medio = [re.sub(r"\s+", " ", m_.group(0)).strip() for m_ in CONDICION.finditer(o)][:2]
    if medio:
        for f in filas:
            f["conditions"] = "; ".join(x for x in (f["conditions"],
                                        "medido con: " + " | ".join(medio)) if x)
            if f["_confianza"] == "alta" and f["metric"] == "Tm":
                f["_confianza"] = "media"       # valor ligado a condiciones del ensayo

    if re.search(r"(?i)\brespectively\b", o):
        for f in filas:
            f["_confianza"] = "baja"
            f["conditions"] = "; ".join(x for x in (f["conditions"],
                "oracion con 'respectively': emparejar valor y enzima a mano") if x)

    # Dos valores distintos para la misma enzima SIN nada que los distinga: ambiguo.
    grupos: dict = {}
    for f in filas:
        if not f["enzyme"]:
            continue
        grupos.setdefault((f["enzyme"], f["mutations"], f["conditions"], f["metric"]), []).append(f)
    for g in grupos.values():
        if len({f["value"] for f in g}) > 1:
            for f in g:
                if f["_confianza"] == "alta":
                    f["_confianza"] = "media"
                f["conditions"] = "; ".join(x for x in (f["conditions"],
                                            "varios valores para la misma enzima en la oracion") if x)
    return filas


# --- Pipeline ---------------------------------------------------------------

def _es_tabla(c: dict) -> bool:
    return bool(json.loads(c["metadata"]).get("es_tabla"))


def extraer(chunks) -> tuple[list[dict], list[str], dict]:
    filas: list[dict] = []
    pendientes: list[str] = []
    stats: Counter = Counter()
    vistos: set = set()
    for c in chunks:
        texto = c["text"]
        if not TOKEN.search(texto):
            continue
        stats["chunks_con_etiqueta_tm"] += 1
        if _es_tabla(c):
            if MUTACION.search(texto) or ENZIMA.search(texto):
                pendientes.append(c["chunk_id"])
            continue
        meta = json.loads(c["metadata"])
        for o in oraciones(texto):
            for f in filas_de_oracion(o):
                # Regla dura: la cita tiene que respaldar el numero.
                if not re.search(rf"(?<![\d.]){re.escape(f['_literal'])}(?![\d])", f["evidence_span"]):
                    stats["descartada_evidencia_sin_valor"] += 1
                    continue
                clave = (c["doc_id"], f["enzyme"], f["mutations"], f["metric"], round(f["value"], 2))
                if clave in vistos:
                    stats["duplicada"] += 1
                    continue
                vistos.add(clave)
                f.update({
                    "record_id": f"{c['chunk_id']}:{f['metric']}:" + hashlib.sha1(
                        f"{f['enzyme']}|{f['mutations']}|{f['value']}|{f['evidence_span'][:80]}"
                        .encode()).hexdigest()[:10],
                    "uniprot": meta.get("uniprot", "") or "",
                    "doc_id": c["doc_id"], "extracted_by": EXTRACTOR, "verified": False,
                    "_chunk_id": c["chunk_id"], "_doi": c.get("doi", ""),
                    "_section": c.get("section", ""), "_title": c.get("title", ""),
                })
                filas.append(f)
                stats[f"patron_{f['_patron']}"] += 1
                stats[f"confianza_{f['_confianza']}"] += 1
    stats["filas"] = len(filas)
    stats["documentos"] = len({f["doc_id"] for f in filas})
    stats["con_mutacion"] = sum(1 for f in filas if f["mutations"])
    stats["sin_enzima"] = sum(1 for f in filas if not f["enzyme"])
    stats["chunks_tabla_pendientes"] = len(pendientes)
    return filas, pendientes, dict(stats)


CAMPOS = ["record_id", "enzyme", "uniprot", "mutations", "metric", "value", "unit",
          "conditions", "doc_id", "evidence_span", "extracted_by", "verified"]


def publicar(db, filas: list[dict], aprobador: str) -> None:
    """MERGE por record_id: se puede repetir sin duplicar. verified sigue en false."""
    import io

    from data_pipeline.curation.registro import registrar_aprobacion

    ruta = "/Volumes/workspace/lab/raw/curation/mutant_stability.jsonl"
    datos = ("\n".join(json.dumps({k: f[k] for k in CAMPOS}, ensure_ascii=False)
                       for f in filas) + "\n").encode()
    db.w.files.upload(ruta, io.BytesIO(datos), overwrite=True)
    print(f"   subido {len(datos) / 1e6:.2f} MB a {ruta}")
    db.sql(f"""
        MERGE INTO {DESTINO} AS d
        USING (SELECT record_id, enzyme, uniprot, mutations, metric,
                      CAST(value AS DOUBLE) AS value, unit, conditions, doc_id,
                      evidence_span, extracted_by, CAST(verified AS BOOLEAN) AS verified
               FROM read_files('{ruta}', format => 'json')) AS n
        ON d.record_id = n.record_id
        -- Una version sin verificar NUNCA pisa una fila ya verificada por una persona.
        WHEN MATCHED AND (NOT coalesce(d.verified, false) OR n.verified) THEN UPDATE SET
          enzyme = n.enzyme, uniprot = n.uniprot, mutations = n.mutations,
          metric = n.metric, value = n.value, unit = n.unit, conditions = n.conditions,
          doc_id = n.doc_id, evidence_span = n.evidence_span, extracted_by = n.extracted_by,
          verified = n.verified
        WHEN NOT MATCHED THEN INSERT
          (record_id, enzyme, uniprot, mutations, metric, value, unit, conditions,
           doc_id, evidence_span, extracted_by, verified, created_at)
          VALUES (n.record_id, n.enzyme, n.uniprot, n.mutations, n.metric, n.value,
                  n.unit, n.conditions, n.doc_id, n.evidence_span, n.extracted_by,
                  n.verified, current_timestamp())""")
    n = db.sql(f"SELECT count(*), count(DISTINCT doc_id) FROM {DESTINO}")[0]
    ver = db.sql(f"SELECT count_if(verified) FROM {DESTINO}")[0][0]
    print(f"   {DESTINO}: {n[0]} filas de {n[1]} documentos ({ver} verificadas)")
    registrar_aprobacion(db, aprobador, f"{len(filas)} filas en mutant_stability aprobadas para publicar "
                         f"({sum(1 for f in filas if f['verified'])} verificadas)",
                         [f["doc_id"] for f in filas][:200], {"filas": len(filas)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revisar", type=int, default=0,
                    help="imprime N filas con su evidencia para verificarlas a mano")
    ap.add_argument("--confianza", choices=("alta", "media", "baja"),
                    help="con --revisar, solo esa confianza")
    ap.add_argument("--aprobar", metavar="NOMBRE",
                    help="publica en mutant_stability (verified sigue en false)")
    ap.add_argument("--desde", metavar="JSON",
                    help="publica estas filas (p. ej. mutant_stability_revisada.json) en vez "
                         "de volver a extraer")
    ap.add_argument("--solo-verificadas", action="store_true",
                    help="con --desde, publica unicamente las filas con verified=true")
    ap.add_argument("--salida", default=str(SALIDA))
    args = ap.parse_args()

    if args.desde:
        filas = json.loads(Path(args.desde).read_text())
        if args.solo_verificadas:
            filas = [f for f in filas if f.get("verified")]
        print(f"Filas a publicar desde {args.desde}: {len(filas)}"
              f"{' (solo verificadas)' if args.solo_verificadas else ''}")
        if not args.aprobar:
            raise SystemExit("Con --desde hay que indicar --aprobar \"Tu Nombre\" (es una publicacion).")
        from data_pipeline.databricks_io import Databricks
        publicar(Databricks(), filas, args.aprobar)
        return

    if not CHUNKS.exists():
        raise SystemExit(f"Falta {CHUNKS}. Corre antes: "
                         "uv run python -m data_pipeline.curation.curar")
    chunks = [json.loads(l) for l in CHUNKS.open()]
    filas, pendientes, stats = extraer(chunks)

    print("=== Extraccion de mutant_stability ===")
    for k in sorted(stats):
        print(f"   {k:34s} {stats[k]}")
    print(f"   metricas: {dict(Counter(f['metric'] for f in filas))}")

    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mutant_stability_propuesta.json").write_text(json.dumps(filas, indent=2, ensure_ascii=False))
    (out / "tablas_pendientes.json").write_text(json.dumps(pendientes, indent=2))
    print(f"\nPropuesta:  {out / 'mutant_stability_propuesta.json'}")
    print(f"Tablas que requieren extraccion asistida o manual: {out / 'tablas_pendientes.json'}")

    if args.revisar:
        sel = [f for f in filas if not args.confianza or f["_confianza"] == args.confianza]
        print(f"\n=== {min(args.revisar, len(sel))} de {len(sel)} filas para verificar ===")
        for f in sel[:args.revisar]:
            print(f"\n[{f['_confianza']}/{f['_patron']}] {f['enzyme'] or '(sin enzima)'} "
                  f"{f['mutations'] or '(sin mutaciones declaradas)'} -> {f['metric']} = "
                  f"{f['value']} {f['unit']}{'  (aprox)' if f['_aproximado'] else ''}"
                  f"{'  [' + f['conditions'] + ']' if f['conditions'] else ''}")
            print(f"   doc: {f['doc_id']}  doi: {f['_doi']}")
            print(f"   evidencia: {f['evidence_span'][:320]}")

    if args.aprobar:
        from data_pipeline.databricks_io import Databricks
        print(f"\nPublicando en {DESTINO} (aprobado por '{args.aprobar}')...")
        publicar(Databricks(), filas, args.aprobar)
    else:
        print("\nNO se escribio en Databricks. Revisa con --revisar N y, si esta bien, "
              "vuelve a correr con --aprobar \"Tu Nombre\".")


if __name__ == "__main__":
    main()
