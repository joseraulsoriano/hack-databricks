"""Revision humana de la propuesta de mutant_stability, con criterio fijo y medible.

El repo exige que una PERSONA verifique (verified = true). Este script no verifica nada por
su cuenta: reduce el margen de juicio libre y deja la decision auditable. Tampoco escribe
en Databricks.

QUE CAMBIA RESPECTO A "marca ok / corregir / descartar a ojo"
  1. Cuatro preguntas de si/no por fila. La decision SE DERIVA de las respuestas; la persona
     no elige "ok" o "descartar": responde hechos comprobables en la frase.
  2. La regla decide QUE se revisa, no la persona: solo filas utilizables (con enzima y sin
     "respectively"); el resto va a filas_no_elegibles.csv con su motivo. Con --muestra se
     revisa una muestra aleatoria estratificada con semilla fija.
  3. Contraste automatico con otros articulos (misma enzima/variante) y marca de si la frase
     lleva una referencia (cita de otro trabajo, no medicion propia).
  4. La etiqueta de confianza del extractor NO aparece en el CSV (es una opinion sin medir y
     sesga hacia aprobar). Se usa despues para medir su precision real.
  5. Motivos de rechazo o correccion de una lista cerrada, no texto libre.

LAS CUATRO PREGUNTAS (responder s / n)
  q1_valor_en_frase    El numero de la columna `valor` aparece tal cual en la frase.
  q2_enzima_en_frase   La frase nombra la enzima/variante de la columna `enzima` y ese valor
                       es SUYO (no de otra enzima nombrada en la misma frase).
  q3_metrica_correcta  `metrica` es la correcta: Tm si es un valor absoluto, dTm si es un
                       incremento respecto a otra cosa.
  q4_condiciones       s = medido en condiciones estandar o la frase no menciona ninguna;
                       a = hay una condicion especial (ion, sal, solvente, pH...) y la anote
                            en `condiciones_corr`;
                       n = hay una condicion especial que no puedo precisar.

DECISION DERIVADA
  todas bien (q4 = s o a)                           -> ok  (con 'a' se guarda la condicion)
  alguna en n, y esa correccion esta rellenada      -> corregir
  alguna en n, y falta la correccion (o q4 = n)     -> descartar
  motivos: q1 valor_no_respaldado, q2 enzima_equivocada, q3 metrica_equivocada,
           q4 condiciones_no_precisables.
  Una correccion de valor debe aparecer literalmente en la frase: la cita respalda el numero.

Flujo
    1. uv run python -m data_pipeline.curation.extraer_mutantes
    2. uv run python -m data_pipeline.curation.revisar_mutantes --exportar [--muestra 40]
    3. La persona responde q1..q4 en revision_mutantes.csv
    4. uv run python -m data_pipeline.curation.revisar_mutantes --aplicar revision_mutantes.csv \\
           --revisor "Tu Nombre"
    5. uv run python -m data_pipeline.curation.extraer_mutantes \\
           --desde data/resultados/curacion/mutant_stability_revisada.json \\
           --solo-verificadas --aprobar "Tu Nombre"
"""

import argparse
import csv
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "data" / "resultados" / "curacion"
GENERICAS = {"petase", "cutinase", "esterase", "lipase"}
UMBRAL_DISCREPANCIA = 5.0          # grados C respecto a la mediana de otros articulos
METRICAS = {"Tm", "dTm"}

# Cita de otro trabajo: "[ 19 ]", "et al., 2016", "( Then et al., 2016 )", "(Perz 2015)".
REFERENCIA = re.compile(
    r"\[\s*\d+(?:\s*[,–\-]\s*\d+)*\s*\]"
    r"|\bet\s+al\.?[\s,]*\(?\s*\d{4}"
    r"|\(\s*[A-Z][A-Za-z\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-]+)?\s*,?\s*\d{4}\s*\)")

PREGUNTAS = ["q1_valor_en_frase", "q2_enzima_en_frase", "q3_metrica_correcta", "q4_condiciones"]
MOTIVO = {"q1_valor_en_frase": "valor_no_respaldado", "q2_enzima_en_frase": "enzima_equivocada",
          "q3_metrica_correcta": "metrica_equivocada", "q4_condiciones": "condiciones_no_precisables"}

COLUMNAS = ["id", "doc_id", "doi", "titulo", "enzima", "mutaciones", "metrica", "valor",
            "unidad", "condiciones", "referencia_en_frase", "contraste", "evidencia",
            *PREGUNTAS, "enzima_corr", "mutaciones_corr", "metrica_corr", "valor_corr",
            "condiciones_corr", "nota_revisor"]


# --- Elegibilidad y contraste -------------------------------------------------

def motivo_no_elegible(f: dict) -> str | None:
    """None si la fila se puede revisar; si no, por que queda fuera."""
    if "respectively" in f["conditions"]:
        return "respectively: emparejar valor y enzima a mano"
    if not f["enzyme"]:
        return "sin_enzima: completar el sujeto antes de revisar"
    return None


def contraste(filas: list[dict], umbral: float) -> dict[str, str]:
    """Por fila elegible: concuerda / discrepa con otros articulos de la misma enzima+variante.

    Sirve para detectar valores raros, NO para confirmar: muchos articulos repiten el
    valor de un mismo trabajo original, por eso se informa cuantos llevan referencia."""
    pool = defaultdict(list)
    for f in filas:
        if f["metric"] != "Tm" or f["enzyme"].lower() in GENERICAS:
            continue
        if "medido con" in f["conditions"] or f["_aproximado"] or "modificacion" in f["conditions"]:
            continue
        pool[(f["enzyme"], f["mutations"])].append(f)
    out = {}
    for f in filas:
        if f["metric"] != "Tm":
            out[f["record_id"]] = "dTm: sin contraste"
            continue
        if f["enzyme"].lower() in GENERICAS:
            out[f["record_id"]] = "enzima generica: agrupa enzimas distintas, no comparable"
            continue
        otros = [g for g in pool[(f["enzyme"], f["mutations"])] if g["doc_id"] != f["doc_id"]]
        if not otros:
            out[f["record_id"]] = "sin comparables en otros articulos"
            continue
        med = statistics.median(g["value"] for g in otros)
        n_art = len({g["doc_id"] for g in otros})
        n_ref = sum(1 for g in otros if REFERENCIA.search(g["evidence_span"]))
        d = abs(f["value"] - med)
        etiqueta = "DISCREPA" if d > umbral else "concuerda"
        especial = (" [esta fila tiene condiciones especiales: comparacion indicativa]"
                    if ("medido con" in f["conditions"] or "modificacion" in f["conditions"]) else "")
        out[f["record_id"]] = (f"{etiqueta}: {f['value']:g} frente a mediana {med:g} de "
                               f"{n_art} art. ({n_ref} con referencia), dif {d:.1f}{especial}")
    return out


def _asignar(estratos: dict[str, list], n: int) -> dict[str, int]:
    """Reparte la muestra entre niveles de confianza: proporcional, minimo 3 (o todo el nivel)."""
    total = sum(len(v) for v in estratos.values())
    asign = {k: min(len(v), max(3, round(n * len(v) / total))) for k, v in estratos.items()}
    return asign


# --- Exportar ---------------------------------------------------------------

def _decisiones_en(destino: Path) -> int:
    """Filas del CSV existente con alguna respuesta (trabajo de una persona)."""
    if not destino.exists():
        return 0
    with destino.open(newline="", encoding="utf-8-sig") as fh:
        return sum(1 for r in csv.DictReader(fh) if any((r.get(q) or "").strip() for q in PREGUNTAS))


def exportar(propuesta: Path, destino: Path, muestra: int | None, semilla: int,
             mostrar_confianza: bool, forzar: bool, umbral: float) -> None:
    hechas = _decisiones_en(destino)
    if hechas and not forzar:
        raise SystemExit(
            f"{destino} ya tiene {hechas} filas respondidas: exportar de nuevo las borraria.\n"
            "Aplicalas primero con --aplicar, o usa --forzar si de verdad quieres empezar de cero.")

    todas = json.loads(propuesta.read_text())
    elegibles, excluidas = [], []
    for f in todas:
        m = motivo_no_elegible(f)
        (excluidas if m else elegibles).append((f, m))
    elegibles = [f for f, _ in elegibles]
    contr = contraste(elegibles, umbral)

    if muestra:
        estratos = defaultdict(list)
        for f in elegibles:
            estratos[f["_confianza"]].append(f)
        asign = _asignar(estratos, muestra)
        rng = random.Random(semilla)
        elegidas = []
        for conf, lista in sorted(estratos.items()):
            lista = sorted(lista, key=lambda f: f["record_id"])      # orden estable antes de sortear
            elegidas += rng.sample(lista, asign[conf])
        print(f"Muestra aleatoria estratificada (semilla {semilla}): "
              + ", ".join(f"{c} {asign[c]}/{len(estratos[c])}" for c in sorted(estratos)))
        revisar = elegidas
    else:
        revisar = elegibles
    revisar = sorted(revisar, key=lambda f: (f["doc_id"], f["record_id"]))

    cols = list(COLUMNAS)
    if mostrar_confianza:
        cols.insert(1, "confianza")
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8-sig") as fh:      # BOM: acentos en Excel
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in revisar:
            fila = {
                "id": f["record_id"], "doc_id": f["doc_id"], "doi": f.get("_doi", ""),
                "titulo": (f.get("_title") or "")[:120], "enzima": f["enzyme"],
                "mutaciones": f["mutations"], "metrica": f["metric"], "valor": f["value"],
                "unidad": f["unit"], "condiciones": f["conditions"],
                "referencia_en_frase": "si" if REFERENCIA.search(f["evidence_span"]) else "no",
                "contraste": contr[f["record_id"]], "evidencia": f["evidence_span"],
            }
            if mostrar_confianza:
                fila["confianza"] = f["_confianza"]
            w.writerow(fila)

    fuera = destino.parent / "filas_no_elegibles.csv"
    with fuera.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "doc_id", "doi", "metrica", "valor", "motivo", "pista", "evidencia"])
        for f, m in sorted(excluidas, key=lambda t: (t[1], t[0]["doc_id"])):
            w.writerow([f["record_id"], f["doc_id"], f.get("_doi", ""), f["metric"], f["value"],
                        m, f["conditions"], f["evidence_span"]])

    disc = sum(1 for f in revisar if contr[f["record_id"]].startswith("DISCREPA"))
    print(f"\nPropuesta: {len(todas)} filas | elegibles: {len(elegibles)} | "
          f"no elegibles: {len(excluidas)} (en {fuera.name})")
    print(f"A revisar: {len(revisar)} filas de {len({f['doc_id'] for f in revisar})} articulos | "
          f"{disc} con DISCREPA respecto a otros articulos")
    print(f"Archivo: {destino}")
    print("Responde q1..q4 con s / n (q4 admite tambien a). Lee el encabezado de este modulo "
          "para el criterio exacto.")


# --- Aplicar ----------------------------------------------------------------

def wilson_inferior(ok: int, n: int, z: float = 1.96) -> float | None:
    """Cota inferior del intervalo de Wilson al 95 % para una proporcion ok/n."""
    if n == 0:
        return None
    p = ok / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centro - margen)


def _num(texto: str):
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def _decidir(r: dict, f: dict, n: int, errores: list[str]):
    """Devuelve (decision, motivos, cambios) o None si la fila esta sin responder."""
    resp = {q: (r.get(q) or "").strip().lower() for q in PREGUNTAS}
    if not any(resp.values()):
        return None
    faltan = [q for q in PREGUNTAS if not resp[q]]
    if faltan:
        errores.append(f"fila {n}: faltan respuestas en {', '.join(faltan)} "
                       "(responde las cuatro o ninguna)")
        return None
    for q in PREGUNTAS[:3]:
        if resp[q] not in ("s", "n"):
            errores.append(f"fila {n}: {q} debe ser s o n (hay {resp[q]!r})")
            return None
    if resp["q4_condiciones"] not in ("s", "a", "n"):
        errores.append(f"fila {n}: q4_condiciones debe ser s, a o n (hay {resp['q4_condiciones']!r})")
        return None

    corr = {c: (r.get(c) or "").strip() for c in
            ("enzima_corr", "mutaciones_corr", "metrica_corr", "valor_corr", "condiciones_corr")}
    cambios: dict = {}
    if corr["valor_corr"]:
        num = _num(corr["valor_corr"])
        if num is None:
            errores.append(f"fila {n}: valor_corr {corr['valor_corr']!r} no es un numero")
            return None
        if f"{num:g}" not in f["evidence_span"]:
            errores.append(f"fila {n}: valor_corr {num:g} no aparece en la frase; "
                           "la cita tiene que respaldar el numero")
            return None
        cambios["value"] = num
    if corr["enzima_corr"]:
        cambios["enzyme"] = corr["enzima_corr"]
    if corr["mutaciones_corr"]:
        cambios["mutations"] = corr["mutaciones_corr"]
    if corr["metrica_corr"]:
        if corr["metrica_corr"] not in METRICAS:
            errores.append(f"fila {n}: metrica_corr debe ser Tm o dTm")
            return None
        cambios["metric"] = corr["metrica_corr"]
    if resp["q4_condiciones"] == "a":
        if not corr["condiciones_corr"]:
            errores.append(f"fila {n}: q4 = a exige anotar la condicion en condiciones_corr")
            return None
        cambios["conditions"] = "; ".join(x for x in (f["conditions"], corr["condiciones_corr"]) if x)
    elif corr["condiciones_corr"]:
        cambios["conditions"] = corr["condiciones_corr"]

    fallos = [q for q in PREGUNTAS if resp[q] == "n"]
    motivos = [MOTIVO[q] for q in fallos]
    if not fallos:
        return ("corregir" if cambios and set(cambios) - {"conditions"} else "ok"), \
            ["ajuste_menor"] if set(cambios) - {"conditions"} else [], cambios

    # Para cada pregunta fallida hace falta SU correccion; si no, la fila no se salva.
    necesaria = {"q1_valor_en_frase": "value", "q2_enzima_en_frase": "enzyme",
                 "q3_metrica_correcta": "metric"}
    for q in fallos:
        if q == "q4_condiciones" or necesaria[q] not in cambios:
            return "descartar", motivos, {}
    return "corregir", motivos, cambios


def aplicar(propuesta: Path, revision: Path, revisor: str, salida: Path,
            minutos: float | None = None) -> None:
    base = {f["record_id"]: f for f in json.loads(propuesta.read_text())}
    with revision.open(newline="", encoding="utf-8-sig") as fh:
        filas_csv = list(csv.DictReader(fh))

    # Si se mejora el extractor despues de exportar, un id puede cambiar aunque la fila sea la
    # misma. Se reconoce tambien por su contenido para no perder el trabajo de la persona.
    por_clave: dict = defaultdict(list)
    for f in base.values():
        por_clave[(f["doc_id"], f["enzyme"], f["mutations"], f["metric"], round(f["value"], 3))].append(f["record_id"])

    errores: list[str] = []
    avisos: list[str] = []
    decididas: dict[str, tuple] = {}
    for n, r in enumerate(filas_csv, start=2):              # fila 1 = encabezado
        rid = r.get("id", "")
        respondida = any((r.get(q) or "").strip() for q in PREGUNTAS)
        if rid not in base:
            val = _num(r.get("valor", ""))
            cand = por_clave.get((r.get("doc_id", ""), r.get("enzima", ""), r.get("mutaciones", ""),
                                  r.get("metrica", ""), round(val, 3) if val is not None else None), [])
            if len(cand) == 1:
                avisos.append(f"fila {n}: el id cambio; reconocida por su contenido ({cand[0]})")
                rid = cand[0]
            elif respondida:
                avisos.append(f"fila {n}: YA NO EXISTE en la propuesta actual (ni por id ni por contenido); "
                              "su respuesta se ignora")
                continue
            else:
                continue
        res = _decidir(r, base[rid], n, errores)
        if res:
            decididas[rid] = (*res, {q: (r.get(q) or "").strip().lower() for q in PREGUNTAS},
                              (r.get("nota_revisor") or "").strip(), r.get("contraste", ""))
    if errores:
        print("NO SE APLICO NADA. Corrige estos errores en el CSV:")
        for e in errores:
            print("  -", e)
        raise SystemExit(1)

    resultado, cuenta, motivos_c, por_contraste = [], defaultdict(Counter), Counter(), defaultdict(Counter)
    for rid, f in base.items():
        f = dict(f)
        if rid in decididas:
            decision, motivos, cambios, resp, nota, contr = decididas[rid]
            cuenta[f["_confianza"]][decision] += 1
            motivos_c.update(motivos)
            clave = "DISCREPA" if contr.startswith("DISCREPA") else (
                "concuerda" if contr.startswith("concuerda") else "sin_contraste")
            por_contraste[clave][decision] += 1
            if decision == "descartar":
                continue
            f.update(cambios)
            f["verified"] = True
            f["_revisor"] = revisor
            f["_respuestas"] = resp
            f["_motivos"] = motivos
            if decision == "corregir":
                f["extracted_by"] = f"human:{revisor} (corregido de agent:regex_extractor)"
            if nota:
                f["_nota_revisor"] = nota
        else:
            cuenta[f["_confianza"]]["sin_revisar"] += 1
        resultado.append(f)

    salida.mkdir(parents=True, exist_ok=True)
    (salida / "mutant_stability_revisada.json").write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False))

    resumen = {"revisor": revisor, "por_confianza": {}, "motivos": dict(motivos_c),
               "contraste_vs_decision": {k: dict(v) for k, v in por_contraste.items()}}
    print(f"\nRevisor: {revisor}")
    print(f"{'confianza':10s} {'ok':>4s} {'corr':>5s} {'desc':>5s} {'sin rev':>8s} "
          f"{'precision':>10s} {'cota inf 95%':>13s}")
    for conf in ("alta", "media", "baja"):
        c = cuenta.get(conf, Counter())
        rev = c["ok"] + c["corregir"] + c["descartar"]
        prec = c["ok"] / rev if rev else None
        cota = wilson_inferior(c["ok"], rev)
        resumen["por_confianza"][conf] = {**dict(c), "revisadas": rev,
                                          "precision_sin_correccion": prec, "cota_inferior_95": cota}
        print(f"{conf:10s} {c['ok']:4d} {c['corregir']:5d} {c['descartar']:5d} {c['sin_revisar']:8d} "
              f"{(f'{prec:.0%}' if prec is not None else '-'):>10s} "
              f"{(f'{cota:.0%}' if cota is not None else '-'):>13s}")
    if motivos_c:
        print("\nMotivos de correccion o rechazo:", dict(motivos_c))
    if por_contraste:
        print("Contraste automatico vs decision humana:")
        for k, v in por_contraste.items():
            tot = sum(v.values())
            print(f"   {k:14s} {dict(v)}  (no-ok: {tot - v['ok']}/{tot})")
    for a in avisos:
        print("  AVISO:", a)
    ver = sum(1 for f in resultado if f.get("verified"))
    resumen["verificadas"], resumen["en_propuesta"] = ver, len(resultado)
    n_rev = len(decididas)
    if minutos and n_rev:
        resumen["tiempo"] = {"minutos": minutos, "filas_revisadas": n_rev,
                             "segundos_por_fila": round(minutos * 60 / n_rev, 1)}
        print(f"\nTiempo: {minutos:g} min para {n_rev} filas = {minutos * 60 / n_rev:.0f} s por fila "
              f"(las {sum(1 for f in base.values() if not motivo_no_elegible(f))} elegibles "
              f"llevarian ~{minutos / n_rev * sum(1 for f in base.values() if not motivo_no_elegible(f)):.0f} min)")
    (salida / "revision_resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(f"\nVerificadas: {ver} de {len(resultado)} en la propuesta revisada")
    print(f"Archivos: {salida / 'mutant_stability_revisada.json'} y revision_resumen.json")
    print("Siguiente: extraer_mutantes --desde ...revisada.json --solo-verificadas --aprobar \"Tu Nombre\"")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--exportar", action="store_true", help="genera el CSV de revision")
    g.add_argument("--aplicar", metavar="CSV", help="aplica las respuestas del CSV relleno")
    ap.add_argument("--muestra", type=int, help="con --exportar: revisar solo N filas elegidas "
                                                "al azar, estratificadas por confianza")
    ap.add_argument("--semilla", type=int, default=42, help="semilla del sorteo de la muestra")
    ap.add_argument("--mostrar-confianza", action="store_true",
                    help="incluye la etiqueta de confianza del extractor (por defecto se oculta)")
    ap.add_argument("--umbral-discrepancia", type=float, default=UMBRAL_DISCREPANCIA,
                    help="grados C de diferencia con la mediana de otros articulos para marcar DISCREPA")
    ap.add_argument("--forzar", action="store_true",
                    help="con --exportar: sobrescribe aunque el CSV ya tenga respuestas")
    ap.add_argument("--revisor", help="con --aplicar: tu nombre")
    ap.add_argument("--minutos", type=float,
                    help="con --aplicar: minutos que tardaste en responder el CSV (mide tiempo por fila)")
    ap.add_argument("--propuesta", default=str(DIR / "mutant_stability_propuesta.json"))
    ap.add_argument("--salida", default=str(DIR))
    args = ap.parse_args()

    propuesta = Path(args.propuesta)
    if not propuesta.exists():
        raise SystemExit(f"Falta {propuesta}. Corre antes: "
                         "uv run python -m data_pipeline.curation.extraer_mutantes")
    if args.exportar:
        exportar(propuesta, Path(args.salida) / "revision_mutantes.csv", args.muestra,
                 args.semilla, args.mostrar_confianza, args.forzar, args.umbral_discrepancia)
    else:
        if not args.revisor:
            raise SystemExit("Con --aplicar indica --revisor \"Tu Nombre\".")
        aplicar(propuesta, Path(args.aplicar), args.revisor, Path(args.salida), args.minutos)


if __name__ == "__main__":
    main()
