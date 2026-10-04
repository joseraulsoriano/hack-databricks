"""Muestra aleatoria de trozos de la propuesta de documents_curated, para revisarlos a ojo.

Es una comprobacion de sentido comun ANTES de aprobar la curacion: ¿el texto se lee bien, la
seccion es la correcta, la licencia y la cita estan, el trozo aporta algo? No sustituye una
revision; da una idea de la calidad con una muestra que nadie elige (semilla fija).

Las marcas automaticas son indicios objetivos y baratos, no un veredicto:
  empieza_en_minuscula   posible corte a mitad de frase
  termina_sin_punto      posible corte a mitad de frase (las tablas y los titulos no cuentan)
  muy_corto              menos de 40 tokens estimados
  muchas_referencias     mas de 4 citas tipo "[ 12 ]" o "et al." (suele ser bibliografia)
  seccion_generica       la seccion es "Texto" o "Resumen" sin mas

Uso: uv run python -m data_pipeline.curation.muestra_trozos [--n 20] [--semilla 42]
"""

import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHUNKS = ROOT / "data" / "resultados" / "curacion" / "chunks.jsonl"
SALIDA = ROOT / "data" / "resultados" / "curacion" / "muestra_trozos.md"
REFS = re.compile(r"\[\s*\d+(?:\s*[,–\-]\s*\d+)*\s*\]|\bet\s+al\.?")


def marcas(c: dict, meta: dict) -> list[str]:
    t = c["text"].strip()
    m = []
    if t[:1].islower():
        m.append("empieza_en_minuscula")
    if not meta.get("es_tabla") and c["section"] != "Resumen" and not re.search(r"[.!?:)\]\"'”]\s*$", t):
        m.append("termina_sin_punto")
    if meta.get("tokens_estimados", 99) < 40:
        m.append("muy_corto")
    if len(REFS.findall(t)) > 4:
        m.append("muchas_referencias")
    if c["section"] in ("Texto", "Resumen"):
        m.append("seccion_generica")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--ancho", type=int, default=420, help="caracteres de texto que se imprimen")
    args = ap.parse_args()

    if not CHUNKS.exists():
        raise SystemExit(f"Falta {CHUNKS}. Corre antes: uv run python -m data_pipeline.curation.curar")
    chunks = [json.loads(l) for l in CHUNKS.open()]
    rng = random.Random(args.semilla)
    muestra = rng.sample(chunks, args.n)

    out = [f"# Muestra de {args.n} trozos (semilla {args.semilla}, de {len(chunks)})\n"]
    resumen_marcas: dict[str, int] = {}
    for i, c in enumerate(muestra, 1):
        meta = json.loads(c["metadata"])
        mk = marcas(c, meta)
        for k in mk:
            resumen_marcas[k] = resumen_marcas.get(k, 0) + 1
        cab = (f"{i:2d}. {c['chunk_id']} | {c['source']}/{c['doc_type']} | {c['year'] or 's/a'} | "
               f"licencia: {c['license']} | seccion: {c['section'][:50]} | "
               f"relevancia: {meta.get('relevancia')} | ~{meta.get('tokens_estimados')} tok"
               f"{' | TABLA' if meta.get('es_tabla') else ''}")
        print(cab)
        print(f"    titulo: {(c['title'] or '')[:110]}")
        print(f"    doi: {c['doi'] or '(sin doi)'}   url: {(c['url'] or '')[:70]}")
        if mk:
            print(f"    marcas: {', '.join(mk)}")
        cuerpo = re.sub(r"\s+", " ", c["text"]).strip()
        print(f"    texto: {cuerpo[:args.ancho]}{'…' if len(cuerpo) > args.ancho else ''}\n")
        out += [f"## {cab}", f"**{c['title']}**  ", f"doi: {c['doi'] or '(sin doi)'}  ",
                f"marcas: {', '.join(mk) or '-'}\n", "```", c["text"].strip(), "```\n"]
    SALIDA.write_text("\n".join(out))
    print("Resumen de marcas automaticas:", resumen_marcas or "ninguna")
    print(f"Texto completo de los {args.n} trozos: {SALIDA}")


if __name__ == "__main__":
    main()
