"""Perfil de workspace.lab.documents_staging. SOLO LECTURA: no escribe ni borra nada.

Paso 1 de la curacion (docs/CURACION.md): cuantificar duplicados, filas sin texto
aprovechable y la distribucion por fuente, tipo y ano, antes de escribir el pipeline.

Uso: uv run python -m data_pipeline.curation.perfil_staging [--json RUTA]
"""

import argparse
import json
from pathlib import Path

from data_pipeline.databricks_io import Databricks

TABLA = "workspace.lab.documents_staging"

# Cada consulta es (clave, titulo, SQL). Todas son SELECT.
CONSULTAS = [
    ("total", "Total de filas", f"SELECT count(*), count(DISTINCT doc_id) FROM {TABLA}"),
    ("por_fuente", "Por fuente y tipo", f"""
        SELECT source, doc_type, count(*) AS docs,
               count_if(length(coalesce(full_text, '')) > 0) AS con_texto_completo,
               count_if(coalesce(doi, '') <> '') AS con_doi,
               count_if(is_open_access) AS acceso_abierto
        FROM {TABLA} GROUP BY ALL ORDER BY docs DESC"""),
    ("por_ano", "Por ano (desde 2015)", f"""
        SELECT year, count(*) FROM {TABLA}
        WHERE year >= 2015 GROUP BY year ORDER BY year"""),
    ("sin_ano", "Filas sin ano", f"SELECT count(*) FROM {TABLA} WHERE year IS NULL"),
    ("doi", "DOIs: distintos y sin DOI", f"""
        SELECT count(DISTINCT doi) FILTER (WHERE coalesce(doi,'') <> '') AS dois_distintos,
               count_if(coalesce(doi,'') = '') AS sin_doi
        FROM {TABLA}"""),
    ("doi_duplicados", "DOIs repetidos: cuantos y en cuantas filas", f"""
        WITH d AS (SELECT doi, count(*) n FROM {TABLA}
                   WHERE coalesce(doi,'') <> '' GROUP BY doi HAVING count(*) > 1)
        SELECT count(*) AS dois_repetidos, sum(n) AS filas_implicadas,
               sum(n) - count(*) AS filas_sobrantes FROM d"""),
    ("doi_cruce", "DOIs repetidos por combinacion de fuentes", f"""
        WITH d AS (SELECT doi, array_join(array_sort(collect_set(source)), '+') AS fuentes
                   FROM {TABLA} WHERE coalesce(doi,'') <> ''
                   GROUP BY doi HAVING count(DISTINCT source) > 1)
        SELECT fuentes, count(*) FROM d GROUP BY fuentes ORDER BY 2 DESC"""),
    ("hash_duplicados", "Copias exactas por content_hash (sin DOI)", f"""
        WITH h AS (SELECT content_hash, count(*) n FROM {TABLA}
                   WHERE coalesce(doi,'') = '' AND coalesce(content_hash,'') <> ''
                   GROUP BY content_hash HAVING count(*) > 1)
        SELECT count(*) AS hashes_repetidos, sum(n) - count(*) AS filas_sobrantes FROM h"""),
    ("sin_texto", "Texto aprovechable", f"""
        SELECT count_if(length(coalesce(abstract,'')) < 50
                        AND length(coalesce(full_text,'')) = 0) AS sin_texto_util,
               count_if(length(coalesce(full_text,'')) > 0) AS con_texto_completo,
               count_if(length(coalesce(abstract,'')) >= 50
                        AND length(coalesce(full_text,'')) = 0) AS solo_resumen
        FROM {TABLA}"""),
    ("licencias", "Licencias declaradas", f"""
        SELECT coalesce(nullif(license, ''), '(sin declarar)') AS licencia, count(*)
        FROM {TABLA} GROUP BY 1 ORDER BY 2 DESC LIMIT 15"""),
    ("secciones", "Documentos con secciones marcadas '## '", f"""
        SELECT count_if(full_text LIKE '%## %') AS con_secciones,
               round(avg(length(full_text)) FILTER (WHERE length(coalesce(full_text,'')) > 0)) AS largo_medio
        FROM {TABLA}"""),
    ("relevancia", "Senal tematica en titulo o resumen", f"""
        SELECT count_if(lower(concat_ws(' ', title, abstract)) RLIKE
                        'petase|pet hydrolase|poly\\\\(ethylene terephthalate\\\\) hydrolase') AS petasa_explicita,
               count_if(lower(concat_ws(' ', title, abstract)) RLIKE
                        'cutinase|mhetase|lipase|esterase') AS otras_hidrolasas,
               count_if(lower(concat_ws(' ', title, abstract)) RLIKE
                        'recycl|circular economy|waste management') AS reciclaje_general
        FROM {TABLA}"""),
    ("quien", "Quien lo trajo", f"SELECT fetched_by, count(*) FROM {TABLA} GROUP BY 1 ORDER BY 2 DESC"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="guarda el perfil crudo en este archivo")
    args = ap.parse_args()

    db = Databricks()
    perfil = {}
    for clave, titulo, sql in CONSULTAS:
        filas = db.sql(sql)
        perfil[clave] = filas
        print(f"\n## {titulo}")
        for f in filas:
            print("   " + " | ".join("" if v is None else str(v) for v in f))

    if args.json:
        destino = Path(args.json)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(perfil, indent=2, ensure_ascii=False))
        print(f"\nPerfil guardado en: {destino}")


if __name__ == "__main__":
    main()
