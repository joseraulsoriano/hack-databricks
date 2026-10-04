"""De donde salen los datos que la puerta de procedencia coteja.

Trae solo lo CITADO. La puerta no necesita el corpus entero: necesita los
documentos y las filas que la hipotesis afirma que la sostienen. Una hipotesis
cita cinco documentos, no cuatro mil, y asi el cotejo cabe en una respuesta HTTP.

`agent_lab.procedencia` no sabe de Databricks a proposito: recibe datos. Este
modulo es el unico que habla con el warehouse, y se puede sustituir por un dict
en las pruebas.
"""

import re

CURATED = "workspace.lab.documents_curated"
STABILITY = "workspace.lab.mutant_stability"
ACTIVITY_ML = "workspace.lab.pet_activity_ml"

# Un identificador legitimo es asi. Lo que no encaje no se consulta: se deja fuera
# y la puerta lo marca como doc_existe=False, que es la respuesta correcta.
ID_VALIDO = re.compile(r"^[A-Za-z0-9_.:+/-]{1,128}$")


def _en_lista(valores) -> str:
    """Literal SQL para un IN (...), solo con identificadores que pasan el filtro."""
    limpios = sorted({v for v in valores if v and ID_VALIDO.match(v)})
    return ", ".join(f"'{v}'" for v in limpios)


def _db(db=None):
    if db is not None:
        return db
    from data_pipeline.databricks_io import Databricks
    return Databricks()


def cargar_corpus(doc_ids, db=None) -> dict:
    """doc_id -> {text, approved_by, source}. El texto es la union de sus trozos.

    Se une con un separador de espacio: un `evidence_span` que cruce el corte entre
    dos trozos seguiria sin casar, y es correcto que no case. El trozo es la unidad
    que una persona aprobo.
    """
    lista = _en_lista(doc_ids)
    if not lista:
        return {}
    filas = _db(db).sql(f"""
        SELECT doc_id,
               concat_ws(' ', collect_list(text))     AS texto,
               max(approved_by)                       AS approved_by,
               max(source)                            AS source
          FROM {CURATED}
         WHERE doc_id IN ({lista})
      GROUP BY doc_id
    """)
    return {f[0]: {"text": f[1] or "", "approved_by": f[2] or "", "source": f[3] or ""}
            for f in filas}


def cargar_registros(record_ids, db=None) -> dict:
    """record_id -> fila de mutant_stability, con su `verified` tal como esta."""
    lista = _en_lista(record_ids)
    if not lista:
        return {}
    filas = _db(db).sql(f"""
        SELECT record_id, value, unit, verified, doc_id, evidence_span, extracted_by
          FROM {STABILITY}
         WHERE record_id IN ({lista})
    """)
    return {f[0]: {"value": f[1], "unit": f[2] or "", "verified": bool(f[3]),
                   "doc_id": f[4] or "", "evidence_span": f[5] or "",
                   "extracted_by": f[6] or ""} for f in filas}


def cargar_columnas(db=None) -> set:
    """Columnas reales de pet_activity_ml. Vacio = no comprobar (no inventar)."""
    try:
        filas = _db(db).sql(f"DESCRIBE {ACTIVITY_ML}")
    except Exception:
        return set()
    return {f[0] for f in filas if f and f[0] and not str(f[0]).startswith("#")}
