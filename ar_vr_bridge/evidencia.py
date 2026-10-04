"""Pasajes curados para que un agente construya una hipótesis con respaldo.

El problema que resuelve: `/api/v1/ask` devuelve `doc_id`, título y score, pero
**no el texto**. Y la puerta de procedencia exige el `evidence_span` *literal*. Sin
pasajes, el agente tendría que inventarse la frase y la puerta se la rechazaría —
hacer una hipótesis era imposible por construcción.

Aquí devolvemos el trozo tal cual está en `documents_curated`, listo para pegar
como `evidence_span` sin tocar un carácter. Dos modos:

- **Sin `doc_id`**: el índice vectorial elige los documentos pertinentes y de cada
  uno se sacan sus mejores trozos. Varias fuentes, que es lo que permite una
  hipótesis con respaldo múltiple en vez de una referencia suelta.
- **Con `doc_id`**: se busca *dentro* de ese documento. Es el equivalente a «mira
  en este paper esta parte»: no tenemos los PDF, pero sí el texto completo troceado
  por secciones (707 documentos del corpus lo traen).

No hay presupuesto de voz aquí: esto no está en el camino de los 1,5 s, así que
puede pagar una consulta SQL para traer el texto.
"""

import re

CURATED = "workspace.lab.documents_curated"

# Un trozo demasiado corto no sirve como evidencia: la puerta exige 40 caracteres
# y un fragmento breve casa con cualquier cosa.
MINIMO = 40
MAXIMO_POR_DOC = 3     # no inundar la respuesta con un solo documento


def _sql(statement: str, params: dict | None = None):
    from data_pipeline.databricks_io import Databricks
    return Databricks().sql(statement, params=params)


def terminos(consulta: str) -> set[str]:
    """Palabras con las que se puntúa el solape. Las cortas no discriminan."""
    return {t for t in re.findall(r"\w+", (consulta or "").lower()) if len(t) > 3}


def puntuar(texto: str, consulta_terminos: set[str]) -> int:
    if not consulta_terminos:
        return 0
    return len(consulta_terminos & {t for t in re.findall(r"\w+", texto.lower()) if len(t) > 3})


def _en_lista(valores) -> str:
    limpios = sorted({v for v in valores if v and "'" not in v})
    return ", ".join(f"'{v}'" for v in limpios)


def _filas(doc_ids=None, seccion: str = "", limite_sql: int = 400) -> list[list]:
    """Trozos curados y aprobados, de unos documentos o del corpus entero."""
    donde = ["approved_by IS NOT NULL", "approved_by <> ''", f"length(text) >= {MINIMO}"]
    params: dict[str, str] = {}
    if doc_ids:
        lista = _en_lista(doc_ids)
        if not lista:
            return []
        donde.append(f"doc_id IN ({lista})")
    if seccion:
        donde.append("lower(section) LIKE :seccion")
        params["seccion"] = f"%{seccion.lower()}%"
    return _sql(f"""SELECT chunk_id, doc_id, source, title, year, doi, url, section, text
                      FROM {CURATED} WHERE {' AND '.join(donde)} LIMIT {limite_sql}""", params or None)


def _pasaje(fila, puntos: int) -> dict:
    chunk_id, doc_id, source, title, year, doi, url, section, text = fila
    return {"chunk_id": chunk_id, "doc_id": doc_id, "source": source or "",
            "title": (title or "").strip(), "year": int(year) if year else None,
            "doi": doi or "", "url": url or "", "section": section or "",
            # Literal, sin recortar ni limpiar: se pega tal cual como evidence_span.
            "evidence_span": (text or "").strip(), "overlap": puntos}


def buscar(query: str, num_results: int = 8, doc_id: str = "", seccion: str = "",
           doc_ids_rag=None) -> list[dict]:
    """Pasajes ordenados por solape con la consulta.

    `doc_ids_rag` son los documentos que el índice vectorial juzgó pertinentes;
    quien llama los pasa para que la selección no sea solo por palabra clave. Sin
    ellos (o con `doc_id`), se busca directamente en el corpus indicado.
    """
    objetivo = [doc_id] if doc_id else (list(doc_ids_rag) if doc_ids_rag else None)
    filas = _filas(objetivo, seccion)
    if objetivo:
        # El filtro vive en el SQL, pero se repite aquí: si un identificador se cae
        # por el saneado, devolver pasajes de otro documento sería una atribución
        # falsa, y es justo lo que la puerta existe para impedir.
        permitidos = set(objetivo)
        filas = [f for f in filas if f[1] in permitidos]
    palabras = terminos(query)

    puntuados = [(puntuar(f[8] or "", palabras), f) for f in filas]
    # Sin consulta (p. ej. volcar una sección entera) no se descarta por solape.
    if palabras:
        puntuados = [(p, f) for p, f in puntuados if p > 0]
    puntuados.sort(key=lambda x: -x[0])

    por_doc: dict[str, int] = {}
    salida: list[dict] = []
    for puntos, f in puntuados:
        if por_doc.get(f[1], 0) >= MAXIMO_POR_DOC:
            continue
        por_doc[f[1]] = por_doc.get(f[1], 0) + 1
        salida.append(_pasaje(f, puntos))
        if len(salida) >= num_results:
            break
    return salida


def documento(doc_id: str) -> dict:
    """Un documento curado entero, trozo a trozo. Para leerlo como se leería el PDF."""
    filas = _filas([doc_id])
    if not filas:
        return {}
    cab = filas[0]
    return {"doc_id": cab[1], "source": cab[2] or "", "title": (cab[3] or "").strip(),
            "year": int(cab[4]) if cab[4] else None, "doi": cab[5] or "", "url": cab[6] or "",
            "chunks": len(filas),
            "sections": sorted({(f[7] or "").strip() for f in filas if f[7]}),
            "passages": [_pasaje(f, 0) for f in filas]}
