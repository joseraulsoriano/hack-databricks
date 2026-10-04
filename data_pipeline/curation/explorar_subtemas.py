"""Explora que subtemas hay en el corpus curado, para ayudar a DEFINIR la taxonomia.

No asigna subtemas definitivos ni escribe en Databricks: propone candidatos con evidencia
(tamano, terminos, ejemplos) para que una persona decida cuales usar.

Dos metodos independientes, para que se contrasten entre si:
  A. Descubrimiento automatico: NMF sobre TF-IDF de titulo + resumen. Encuentra agrupaciones
     sin que yo le diga que buscar. Se elige el numero de temas por ESTABILIDAD (se repite con
     varias semillas y se mide cuanto coinciden las asignaciones), no por gusto.
  B. Temas candidatos por palabras clave, escritos de antemano a partir del dominio. Sirven
     para comprobar si lo que A encuentra coincide con lo que un experto esperaria, y para medir
     cuantos documentos pertenecen a MAS de un tema (importa para el diseno de la columna).

Alcance: se analizan por separado el NUCLEO (PET y enzima en titulo/resumen) y lo periferico
(plasticos sin enzima, fuera de alcance), porque mezclarlos contamina los temas.

Uso: uv run python -m data_pipeline.curation.explorar_subtemas [--k 10] [--solo-nucleo]
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import numpy as np
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics import adjusted_rand_score

ROOT = Path(__file__).resolve().parents[2]
CHUNKS = ROOT / "data" / "resultados" / "curacion" / "chunks.jsonl"
SALIDA = ROOT / "data" / "resultados" / "curacion" / "subtemas"

# Palabras que aparecen en casi todo el corpus: si se dejan, todos los temas se llaman "PET enzima".
DOMINIO = {"pet", "enzyme", "enzymes", "petase", "petases", "polyethylene", "terephthalate", "poly",
           "ethylene", "hydrolase", "hydrolases", "degradation", "degrading", "degrade", "plastic",
           "plastics", "study", "studies", "using", "used", "use", "results", "review", "novel",
           "paper", "abstract", "al", "et", "based", "high", "new", "also", "however", "show",
           "shown", "demonstrated", "reported", "recent", "recently", "approach", "potential"}
STOP = list(ENGLISH_STOP_WORDS | DOMINIO)

TEMAS = {
    "termoestabilidad": r"thermostab|thermal stab|melting temperature|\bT\s?m\b|disulfide|thermophil|heat[- ]resist",
    "evolucion_dirigida_ml_diseno": r"directed evolution|machine learning|deep learning|rational design|computational design|"
                                    r"protein engineering|mutagenesis|generative|language model|FAST-PETase|DuraPETase|ThermoPETase|engineered variant",
    "descubrimiento_metagenomica": r"metagenom|genome mining|uncultured|novel (?:PET )?hydrolase|screening|isolat|compost|marine|"
                                   r"bioprospect|discover",
    "estructura_mecanismo": r"crystal structure|X-ray|catalytic triad|active site|molecular dynamics|docking|"
                            r"cryo-EM|QM/MM|binding (?:site|cleft)|catalytic mechanism",
    "sustrato_cristalinidad": r"crystallinity|amorphous|PET film|PET powder|PET bottle|textile|polyester fabric|"
                              r"glass transition|pretreatment|micronization|nanoparticle",
    "productos_reciclaje_valorizacion": r"terephthalic acid|\bTPA\b|\bMHET\b|\bBHET\b|monomer|recycl|upcycl|valori[sz]|"
                                        r"circular|closed[- ]loop|value-added|bio-?based",
    "cutinasas_lcc_enzimas_relacionadas": r"cutinase|\bLCC\b|leaf-branch compost|MHETase|esterase|lipase|carboxylesterase|"
                                          r"Thermobifida|TfCut|polyesterase",
    "produccion_expresion_inmovilizacion": r"immobili[sz]|heterologous expression|secretion|whole[- ]cell|surface display|"
                                           r"Pichia|fusion protein|carbohydrate-binding|binding module|anchor|yeast display",
    "microorganismos_biodegradacion": r"Ideonella|bacteri|\bstrain|fungi|fungal|biodegrad|microbial communit|consortium|biofilm",
    "cinetica_ensayos_deteccion": r"kinetic|\bk\s?cat\b|\bK\s?m\b|activity assay|HPLC|fluorescen|biosensor|detection|quantif|high-throughput",
    "contexto_ambiental_microplasticos": r"microplastic|nanoplastic|pollution|ecosystem|toxic|human health|marine litter|"
                                         r"waste management|environmental impact",
    "proceso_industrial_escala": r"industrial|scale[- ]up|reactor|techno-economic|life[- ]cycle|large[- ]scale|pilot|process intensif",
    "quimico_catalitico_no_enzimatico": r"catalyst|glycolysis|hydrogenolysis|chemical recycling|pyrolysis|solvolysis|"
                                        r"metal-organic|photocataly|methanolysis",
}
TEMAS_RE = {k: re.compile(v, re.I) for k, v in TEMAS.items()}


def cargar_docs() -> list[dict]:
    docs: dict[str, dict] = {}
    for linea in CHUNKS.open():
        c = json.loads(linea)
        d = docs.setdefault(c["doc_id"], {
            "doc_id": c["doc_id"], "source": c["source"], "doc_type": c["doc_type"],
            "title": c["title"] or "", "year": c["year"],
            "relevancia": json.loads(c["metadata"]).get("relevancia"), "resumen": "", "cuerpo": ""})
        if c["section"] == "Resumen":
            d["resumen"] += " " + c["text"]
        elif len(d["cuerpo"]) < 1500:
            d["cuerpo"] += " " + c["text"]
    for d in docs.values():
        base = d["resumen"] if len(d["resumen"]) > 120 else d["title"] + " " + d["cuerpo"][:1500]
        d["texto"] = re.sub(r"\s+", " ", base).strip()
    return list(docs.values())


def elegir_k(X, ks, semillas=4) -> tuple[int, dict]:
    """Estabilidad: ARI medio entre las asignaciones de distintas semillas (1 = identicas)."""
    est = {}
    for k in ks:
        asign = []
        for s in range(semillas):
            W = NMF(n_components=k, init="nndsvda", random_state=s, max_iter=400).fit_transform(X)
            asign.append(W.argmax(axis=1))
        est[k] = float(np.mean([adjusted_rand_score(a, b) for a, b in combinations(asign, 2)]))
    return max(est, key=est.get), est


def temas_nmf(docs: list[dict], k: int | None, ks) -> dict:
    vec = TfidfVectorizer(stop_words=STOP, ngram_range=(1, 2), min_df=6, max_df=0.35,
                          sublinear_tf=True)
    X = vec.fit_transform([d["texto"] for d in docs])
    est = None
    if k is None:
        k, est = elegir_k(X, ks)
    nmf = NMF(n_components=k, init="nndsvda", random_state=0, max_iter=600)
    W = nmf.fit_transform(X)
    vocab = np.array(vec.get_feature_names_out())
    asign = W.argmax(axis=1)
    out = {"k": k, "estabilidad": est, "temas": []}
    anos = np.array([d["year"] or 0 for d in docs])
    for t in range(k):
        idx = np.where(asign == t)[0]
        top = vocab[np.argsort(-nmf.components_[t])[:10]].tolist()
        ej = idx[np.argsort(-W[idx, t])[:3]] if len(idx) else []
        out["temas"].append({
            "tema": t, "docs": int(len(idx)), "terminos": top,
            "ano_mediano": int(np.median(anos[idx][anos[idx] > 0])) if len(idx) else None,
            "ejemplos": [docs[i]["title"][:95] for i in ej]})
    # solapamiento: documentos donde el segundo tema pesa casi como el primero
    orden = np.sort(W, axis=1)
    pos = orden[:, -1] > 0
    out["multitema_nmf"] = float(np.mean(orden[pos, -2] >= 0.6 * orden[pos, -1]))
    out["asignacion"] = {docs[i]["doc_id"]: (int(asign[i]), float(W[i, asign[i]])) for i in range(len(docs))}
    return out


def temas_claves(docs: list[dict]) -> dict:
    cuenta, por_doc = Counter(), {}
    for d in docs:
        hit = [k for k, r in TEMAS_RE.items() if r.search(d["title"] + " " + d["texto"])]
        por_doc[d["doc_id"]] = hit
        cuenta.update(hit)
    n_temas = Counter(len(h) for h in por_doc.values())
    pares = Counter()
    for h in por_doc.values():
        pares.update(combinations(sorted(h), 2))
    return {"cuenta": dict(cuenta), "docs_por_n_temas": dict(sorted(n_temas.items())),
            "pares_frecuentes": [(a, b, n) for (a, b), n in pares.most_common(8)], "por_doc": por_doc}


def imprimir_nmf(res: dict, etiqueta: str) -> None:
    print(f"\n=== A. Temas descubiertos ({etiqueta}): k = {res['k']} ===")
    if res["estabilidad"]:
        print("   estabilidad por k (1 = asignaciones identicas entre semillas):",
              {k: round(v, 2) for k, v in res["estabilidad"].items()})
    print(f"   documentos con un segundo tema casi tan fuerte como el primero: {res['multitema_nmf']:.0%}")
    for t in sorted(res["temas"], key=lambda x: -x["docs"]):
        print(f"\n   tema {t['tema']:2d} | {t['docs']:4d} docs | ano mediano {t['ano_mediano']}")
        print(f"      terminos: {', '.join(t['terminos'])}")
        for e in t["ejemplos"]:
            print(f"      - {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, help="numero de temas (por defecto se elige por estabilidad)")
    ap.add_argument("--solo-nucleo", action="store_true", help="omite el analisis de la periferia")
    args = ap.parse_args()
    if not CHUNKS.exists():
        raise SystemExit(f"Falta {CHUNKS}. Corre antes: uv run python -m data_pipeline.curation.curar")

    docs = [d for d in cargar_docs() if d["doc_type"] not in ("structure", "prediction")]
    SALIDA.mkdir(parents=True, exist_ok=True)
    grupos = {"nucleo": [d for d in docs if d["relevancia"] in ("nucleo_pet_enzima", "enzima_plasticos_sin_pet")],
              "periferia": [d for d in docs if d["relevancia"] in ("plasticos_sin_enzima", "fuera_de_alcance")]}
    print(f"Documentos de literatura/datos: {len(docs)} | nucleo: {len(grupos['nucleo'])} | "
          f"periferia: {len(grupos['periferia'])}")

    resultado = {}
    for nombre, lista in grupos.items():
        if nombre == "periferia" and args.solo_nucleo:
            continue
        k = args.k if nombre == "nucleo" else (args.k or None)
        nmf = temas_nmf(lista, k, ks=(6, 8, 10, 12, 14) if nombre == "nucleo" else (4, 6, 8))
        imprimir_nmf(nmf, nombre)
        claves = temas_claves(lista)
        print(f"\n=== B. Temas candidatos por palabras clave ({nombre}, {len(lista)} docs) ===")
        for t, n in sorted(claves["cuenta"].items(), key=lambda kv: -kv[1]):
            print(f"   {n:5d} ({n / len(lista):4.0%})  {t}")
        print(f"   documentos por numero de temas distintos: {claves['docs_por_n_temas']}")
        print(f"   pares que mas coinciden: {[(a[:18], b[:18], n) for a, b, n in claves['pares_frecuentes'][:5]]}")
        resultado[nombre] = {"nmf": {k_: v for k_, v in nmf.items() if k_ != "asignacion"},
                             "claves": {k_: v for k_, v in claves.items() if k_ != "por_doc"}}
        with (SALIDA / f"docs_{nombre}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["doc_id", "titulo", "anio", "relevancia", "tema_nmf", "peso_nmf", "temas_clave"])
            for d in lista:
                tn, pw = nmf["asignacion"][d["doc_id"]]
                w.writerow([d["doc_id"], d["title"][:200], d["year"], d["relevancia"], tn,
                            round(pw, 3), "|".join(claves["por_doc"][d["doc_id"]])])
    (SALIDA / "exploracion.json").write_text(json.dumps(resultado, indent=2, ensure_ascii=False))
    print(f"\nArchivos en {SALIDA}: exploracion.json, docs_nucleo.csv, docs_periferia.csv")


if __name__ == "__main__":
    main()
