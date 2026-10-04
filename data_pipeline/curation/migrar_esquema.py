"""Anade a workspace.lab.documents_curated las columnas de etiquetas por trozo.

Editar el CREATE TABLE IF NOT EXISTS de sql/001_schema.sql no modifica una tabla que ya existe,
asi que las columnas nuevas se anaden con un ALTER TABLE. Este script es IDEMPOTENTE: mira que
columnas faltan y solo anade esas, de modo que se puede correr las veces que haga falta.

Por defecto solo MUESTRA lo que haria (dry-run). Con --aplicar ejecuta el ALTER.

Columnas (nombres en ingles, como el resto de la tabla; valores en espanol):
  relevance           nucleo_pet_enzima | enzima_plasticos_sin_pet | plasticos_sin_enzima |
                      fuera_de_alcance | estructura. Filtro por defecto recomendado para el RAG:
                      relevance IN ('nucleo_pet_enzima','enzima_plasticos_sin_pet','estructura').
  language            en | es | pt | de | fr | cjk | cirilico | desconocido
  subtopic            uno de los 7 subtemas aprobados (ver etiquetar.SUBTEMAS) o NULL
  subtopic_secondary  segundo subtema si pesa casi como el primero, o NULL
  evidence_type       resumen | tabla | estructura | prediccion | introduccion | metodos |
                      resultados | discusion | otro  (deriva del nombre de la seccion)
  enzyme              nombres de enzima especificos que aparecen en el trozo

Uso:
    uv run python -m data_pipeline.curation.migrar_esquema            # dry-run
    uv run python -m data_pipeline.curation.migrar_esquema --aplicar
"""

import argparse

from data_pipeline.databricks_io import Databricks

TABLA = "workspace.lab.documents_curated"
COLUMNAS = {
    "relevance": ("STRING", "Nivel de relevancia del documento (titulo + resumen); filtro por defecto del RAG"),
    "language": ("STRING", "Idioma detectado por heuristica: en, es, pt, de, fr, cjk, cirilico, desconocido"),
    "subtopic": ("STRING", "Subtema principal del trozo (7 aprobados) o NULL si ninguno llega al umbral"),
    "subtopic_secondary": ("STRING", "Segundo subtema si pesa casi como el primero, o NULL"),
    "evidence_type": ("STRING", "Tipo de evidencia derivado de la seccion: resumen, tabla, metodos, resultados..."),
    "enzyme": ("ARRAY<STRING>", "Enzimas especificas nombradas en el trozo (no incluye PETase generica)"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="ejecuta el ALTER TABLE (por defecto, dry-run)")
    args = ap.parse_args()

    db = Databricks()
    existentes = {r[0] for r in db.sql(f"DESCRIBE {TABLA}") if r[0] and not r[0].startswith("#")}
    filas = db.sql(f"SELECT count(*) FROM {TABLA}")[0][0]
    faltan = {c: v for c, v in COLUMNAS.items() if c not in existentes}
    print(f"{TABLA}: {len(existentes)} columnas, {filas} filas")
    if not faltan:
        print("Ya tiene todas las columnas. Nada que hacer.")
        return
    for c, (tipo, comentario) in faltan.items():
        print(f"  + {c} {tipo}  -- {comentario}")
    sql = (f"ALTER TABLE {TABLA} ADD COLUMNS (" +
           ", ".join(f"{c} {t} COMMENT '{com}'" for c, (t, com) in faltan.items()) + ")")
    if not args.aplicar:
        print(f"\nDRY-RUN. Sentencia que se ejecutaria:\n  {sql}\nUsa --aplicar para ejecutarla.")
        return
    db.sql(sql)
    despues = {r[0] for r in db.sql(f"DESCRIBE {TABLA}") if r[0] and not r[0].startswith("#")}
    print(f"\nHecho. Columnas ahora: {len(despues)}. Faltan: {sorted(set(COLUMNAS) - despues) or 'ninguna'}")


if __name__ == "__main__":
    main()
