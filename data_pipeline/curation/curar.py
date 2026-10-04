"""Curacion: workspace.lab.documents_staging -> documents_curated (troceado y citable).

Paso 2 de docs/CURACION.md. Por defecto NO escribe en Databricks: deja la propuesta
en local para que la persona curadora la revise. Solo con --aprobar se publica.

Decisiones y su criterio (registradas en el informe de cada corrida):

  0. ALCANCE DE LA DEDUPLICACION: solo entre registros del mismo tipo (articulo, preprint,
     tesis, publicacion). Una estructura PDB o una prediccion NUNCA se absorbe: su DOI es el del
     articulo que la describe y no es un duplicado (la primera version perdia 201 de 301).
     Siguen sin colapsarse las versiones de un mismo dataset de Zenodo (cada una con su DOI).

  1. DEDUPLICAR por doi normalizado. Gana la fila de Europe PMC con full_text; si
     no hay, la de mayor cantidad de texto. Las absorbidas se anotan en
     metadata.duplicate_of para no perder la trazabilidad.
     Medido: 1 016 doi repetidos en 2 222 filas -> sobran 1 206.
     Las filas sin doi se deduplican por content_hash (solo 3 casos).

  2. DESCARTAR unicamente lo que no tiene texto aprovechable: resumen < 50
     caracteres y sin texto completo (638 filas), mas los textos de relleno que
     solo dicen que el articulo esta en PDF. No se borra nada de staging.

  3. ETIQUETAR el ruido tematico en vez de filtrarlo (metadata.relevancia):
     petasa | hidrolasa_relacionada | contexto_reciclaje | sin_senal.
     La consulta fue amplia a proposito y la mayoria de filas solo trae resumen:
     filtrar por palabras tirarian articulos buenos. Las cutinasas, ademas, son
     el punto de partida de muchas PETasas de ingenieria.

  4. TROCEAR por seccion ('## Titulo', como las deja el conector), objetivo
     ~800 tokens con 15% de solape. Las TABLAS NO SE PARTEN: el conector aplana
     cada <table-wrap> en UNA linea que empieza por 'Table N' (verificado en 383
     documentos), asi que esa linea viaja entera en su propio trozo. Ahi estan
     los valores de Tm, Km y kcat que alimentan mutant_stability.

  4b. NO SE INDEXAN: trozos que son solo el titulo de una seccion, trozos de menos de 15 tokens
     (salvo tablas y resumenes) y secciones administrativas (disponibilidad de datos, informacion
     de contribuyentes, notas del editor, consentimiento...). Un trozo descartado conserva su
     numero en chunk_id, asi los identificadores de los demas no cambian al afinar criterios.
     Titulos y texto se limpian de HTML (&lt;i&gt;, <sub>) al emitir.

  4c. relevancia (metadata) se calcula sobre TITULO + RESUMEN, no sobre el texto completo:
     nucleo_pet_enzima | enzima_plasticos_sin_pet | plasticos_sin_enzima | fuera_de_alcance
     | estructura. Mirar el texto completo sobreetiquetaba cualquier articulo que citara una
     PETasa de pasada. Es una heuristica de palabras clave, no una clasificacion verificada.

  5. CONSERVAR license tal como la declara la fuente, normalizando solo la forma
     ('cc by' y 'cc-by' son la misma). Sin licencia queda '(sin declarar)': no
     todo el acceso abierto es CC-BY y el informe final tiene que poder decirlo.

Uso:
    uv run python -m data_pipeline.curation.curar                  # propuesta, sin escribir
    uv run python -m data_pipeline.curation.curar --limite 300     # prueba rapida
    uv run python -m data_pipeline.curation.curar --aprobar "Jose Soriano"
"""

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SALIDA = ROOT / "data" / "resultados" / "curacion"
ORIGEN = "workspace.lab.documents_staging"
DESTINO = "workspace.lab.documents_curated"

# --- Reglas -----------------------------------------------------------------

MIN_RESUMEN = 50          # caracteres de resumen por debajo de los cuales no aporta
OBJETIVO_TOKENS = 800     # tamano de trozo buscado
MAX_TOKENS = 1000
SOLAPE = 0.15
CHARS_POR_TOKEN = 4       # estimacion estandar, evita depender de un tokenizador

# Deduplicar por DOI solo tiene sentido entre registros del MISMO tipo de cosa. Un articulo
# (Europe PMC / OpenAlex / Zenodo-publicacion) aparece una vez por fuente y SI es duplicado;
# una estructura PDB comparte el DOI del articulo que la describe y no lo es.
LITERATURA = {"article", "preprint", "thesis", "publication"}
NUNCA_DEDUP = {"structure", "prediction"}

MIN_TOKENS = 15           # por debajo de esto (sin ser tabla ni resumen) el trozo no aporta
# Secciones administrativas: no responden preguntas cientificas y ensucian la busqueda.
ADMIN = re.compile(
    r"(?i)^(authors?['\u2019]?\s*contributions?|author information|contributor information|"
    r"data availability|code availability|materials? availability|availability of|"
    r"reporting summary|competing interests?|declarations?|disclosures?|ethic|supplementary|"
    r"supporting information|associated data|abbreviations?|glossary|additional information|"
    r"peer review|publisher['\u2019]?s note|open access|credit|institutional review|"
    r"informed consent|consent to)")


def limpiar_html(texto):
    """Titulos y texto llegan con '&lt;i&gt;' (doble escape) y '<sub>cat</sub>': se limpian al emitir."""
    if not texto:
        return texto
    t = html.unescape(html.unescape(texto))
    t = re.sub(r"</?(?:sub|sup)>", "", t)                     # k<sub>cat</sub> -> kcat
    t = re.sub(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>", "", t)
    return re.sub(r"[ \t]{2,}", " ", t)


# Exclusion manual, con su criterio registrado en el informe: texto ajeno al dominio que
# se colo en la busqueda (11 articulos de OpenAlex y 2 registros de software de Zenodo
# con el mismo titulo, sin relacion con enzimas ni plasticos).
EXCLUIR_TITULO = re.compile(r"(?i)zimmerman framework")
# Los datasets se publican por versiones (cada una con su DOI) y OpenAlex las replica: el DOI
# no las agrupa, el titulo identico si.
DATASET_TIPOS = {"dataset", "software", "model"}

_PALABRAS = {
    "en": "the of and to in is for that with as on by are this from or an be at which we it can has have was were not".split(),
    "es": "el la los las de del que en un una para por con se su es al lo como más pero sus le ya o este porque esta entre cuando muy sin sobre también hasta hay donde desde todo durante todos".split(),
    "pt": "o a os as de do da dos das que em um uma para por com se não mais como mas ao aos é são foi pelo pela".split(),
    "de": "der die das und ist nicht mit den von zu für auf ein eine dem des sich auch als bei werden wird sind aus nach wie oder im zum zur über".split(),
    "fr": "le la les des est une pour dans qui que sur par avec au aux du ce cette sont plus ou mais comme leur".split(),
}


def detectar_idioma(texto: str) -> str:
    """Idioma por escritura y palabras frecuentes. Es una heuristica barata, no un detector:
    sirve para filtrar el corpus, y 'desconocido' significa que no hay texto suficiente."""
    t = (texto or "")[:1500]
    letras = [c for c in t if c.isalpha()]
    n = len(letras)
    # El chino, japones y coreano dicen mucho con pocos caracteres: umbral propio.
    if n >= 12 and sum("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7af" for c in letras) / n > 0.3:
        return "cjk"
    if n < 40:
        return "desconocido"
    if sum("\u0400" <= c <= "\u04ff" for c in letras) / n > 0.2:
        return "cirilico"
    palabras = re.findall(r"[a-zà-ÿ]+", t.lower())[:300]
    if not palabras:
        return "desconocido"
    pts = {k: sum(w in set(v) for w in palabras) for k, v in _PALABRAS.items()}
    mejor = max(pts, key=pts.get)
    return mejor if pts[mejor] >= 0.12 * len(palabras) else "desconocido"


RELLENO = re.compile(r"(?i)full text of this (preprint|article) is available as a pdf")
LINEA_TABLA = re.compile(r"(?i)^(?:supplementary\s+|suppl\.?\s+|extended\s+data\s+)?"
                         r"(?:table|tabla|cuadro)\s*\.?\s*\d")


def parece_tabla(linea: str) -> bool:
    """Tabla aplanada cuyo rotulo no reconocemos.

    El conector une las celdas con espacios, asi que una tabla es una linea
    larga, densa en digitos y simbolos, y casi sin frases. Detectarlas importa
    porque son las que NO hay que partir: ahi estan los Tm, Km y kcat.
    """
    if LINEA_TABLA.match(linea):
        return True
    if len(linea) < 400:
        return False
    densidad = sum(c.isdigit() or c in "±×−-/()[]%" for c in linea) / len(linea)
    frases = len(re.findall(r"[.;]\s+[A-Z]", linea))
    return densidad > 0.12 and frases < len(linea) / 600
SENALES = [
    ("petasa", re.compile(r"(?i)petase|pet hydrolase|poly\(ethylene terephthalate\) hydrolase"
                          r"|ideonella|is ?petase|fast-petase|leaf-branch compost cutinase|lcc")),
    ("hidrolasa_relacionada", re.compile(r"(?i)cutinase|mhetase|lipase|esterase|carboxylesterase"
                                         r"|hydrolase|depolymeras")),
    ("contexto_reciclaje", re.compile(r"(?i)recycl|circular economy|waste management|microplastic"
                                      r"|biodegrad|plastic")),
]


def normalizar_licencia(lic: str) -> str:
    """Unifica la forma, no el contenido: 'cc by' y 'cc-by-4.0' -> 'cc-by'."""
    lic = (lic or "").strip().lower()
    if not lic:
        return "(sin declarar)"
    lic = re.sub(r"[\s_]+", "-", lic)
    lic = re.sub(r"-(\d+(\.\d+)?)$", "", lic)        # quita la version: cc-by-4.0 -> cc-by
    return lic


def relevancia(titulo: str, resumen: str, texto: str) -> str:
    base = f"{titulo} {resumen} {texto[:4000]}"
    for etiqueta, patron in SENALES:
        if patron.search(base):
            return etiqueta
    return "sin_senal"


def tokens(texto: str) -> int:
    return max(1, len(texto) // CHARS_POR_TOKEN)


def partir_seccion(cuerpo: str) -> list[str]:
    """Trocea el cuerpo de una seccion respetando lineas y sin partir tablas."""
    lineas = [ln for ln in cuerpo.split("\n") if ln.strip()]
    trozos: list[str] = []
    actual: list[str] = []

    def cerrar():
        if actual:
            trozos.append("\n".join(actual))
            actual.clear()

    for linea in lineas:
        if parece_tabla(linea.strip()):
            cerrar()
            trozos.append(linea.strip())          # la tabla viaja entera y sola
            continue
        if tokens("\n".join(actual + [linea])) > MAX_TOKENS and actual:
            # Solape: el ultimo parrafo pasa tambien al trozo siguiente si es corto.
            ultimo = actual[-1]
            cerrar()
            if (tokens(ultimo) <= OBJETIVO_TOKENS * SOLAPE * 2
                    and tokens(ultimo + "\n" + linea) <= MAX_TOKENS):
                actual.append(ultimo)
        if tokens(linea) > MAX_TOKENS:            # parrafo enorme: por frases
            cerrar()
            trozos.extend(_partir_prosa(linea))
            continue
        actual.append(linea)
    cerrar()
    return [t for t in trozos if t.strip()]


def _partir_prosa(linea: str) -> list[str]:
    """Parte un parrafo largo por frases, con una frase de solape. Si una frase
    sola excede el maximo (texto sin puntos), corta por palabras."""
    piezas: list[str] = []
    for fr in re.split(r"(?<=[.;])\s+", linea):
        if tokens(fr) <= MAX_TOKENS:
            piezas.append(fr)
            continue
        palabras, paso = fr.split(), OBJETIVO_TOKENS * CHARS_POR_TOKEN // 7
        piezas.extend(" ".join(palabras[i:i + paso]) for i in range(0, len(palabras), paso))
    trozos, buf = [], []
    for p in piezas:
        if tokens(" ".join(buf + [p])) > OBJETIVO_TOKENS and buf:
            trozos.append(" ".join(buf))
            buf = buf[-1:] if tokens(buf[-1]) < OBJETIVO_TOKENS * SOLAPE * 2 else []
        buf.append(p)
    if buf:
        trozos.append(" ".join(buf))
    return trozos


def trocear(doc: dict) -> list[tuple[str, str, str | None]]:
    """Devuelve [(seccion, texto, motivo_descarte)].

    Un trozo descartado SIGUE ocupando su numero en chunk_id: asi los identificadores de
    los demas no cambian al afinar los criterios (lo que depende de ellos, como la
    muestra de revision de mutant_stability, sigue siendo valido)."""
    titulo, resumen = doc.get("title", ""), doc.get("abstract", "")
    texto = doc.get("full_text") or ""
    salida: list[tuple[str, str, str | None]] = []
    if resumen and len(resumen) >= MIN_RESUMEN:
        # Algunos "resumenes" de OpenAlex traen el texto entero: tambien se trocean.
        for trozo in partir_seccion(f"{titulo}\n{resumen}".strip()):
            salida.append(("Resumen", trozo, None))
    if texto and not RELLENO.search(texto):
        partes = re.split(r"(?m)^## ", texto)
        for parte in partes:
            if not parte.strip():
                continue
            cabeza, _, cuerpo = parte.partition("\n")
            seccion = cabeza.strip() or "Texto"
            if re.match(r"(?i)^(references|bibliography|acknowledge?ments?|footnotes|"
                        r"author contributions|conflicts? of interest|funding|biograph)",
                        seccion):
                continue                      # no aportan al RAG y ensucian la busqueda
            for trozo in partir_seccion(cuerpo if cuerpo.strip() else parte):
                motivo = None
                if not cuerpo.strip():
                    motivo = "solo_titulo_de_seccion"
                elif ADMIN.match(seccion):
                    motivo = "seccion_administrativa"
                elif tokens(trozo) < MIN_TOKENS and not parece_tabla(trozo.strip()):
                    motivo = "muy_corto"
                salida.append((seccion[:200], trozo, motivo))
    return salida


# --- Relevancia a nivel de documento (titulo + resumen), no de texto completo -----------

_PET = re.compile(r"(?i)\bPET\b(?!\s*[/-]\s*(?:CT|MRI|scan|imaging))|polyethylene[- ]terephthalate"
                  r"|poly\(ethylene terephthalate\)|terephthal")
_ENZ = re.compile(r"(?i)enzym|hydrolas|PETase|cutinase|esterase|lipase|depolymeras|MHETase"
                  r"|polyesterase")
_PLAST = re.compile(r"(?i)plastic|polymer|recycl|polyester|bioplastic|upcycl")


def relevancia_doc(doc_type: str, titulo: str, resumen: str, texto: str) -> str:
    """Nucleo = PET y enzima en titulo/resumen. Mirar el texto completo sobreetiquetaba:
    cualquier articulo que cite una PETasa de pasada salia como 'petasa'."""
    if doc_type in NUNCA_DEDUP:
        return "estructura"
    base = f"{titulo} {resumen}" if len(resumen) >= MIN_RESUMEN else f"{titulo} {texto[:1500]}"
    pet, enz, plast = _PET.search(base), _ENZ.search(base), _PLAST.search(base)
    if pet and enz:
        return "nucleo_pet_enzima"
    if enz and plast:
        return "enzima_plasticos_sin_pet"
    if pet or plast:
        return "plasticos_sin_enzima"
    return "fuera_de_alcance"


# --- Deduplicacion ----------------------------------------------------------

def puntuar(doc: dict) -> tuple:
    """Mayor es mejor: Europe PMC con texto completo gana."""
    tiene_texto = bool(doc.get("full_text")) and not RELLENO.search(doc.get("full_text") or "")
    return (int(tiene_texto),
            int(doc.get("source") == "europepmc"),
            len(doc.get("full_text") or ""),
            len(doc.get("abstract") or ""))


def deduplicar(docs: list[dict]) -> tuple[list[dict], dict]:
    grupos: dict[str, list[dict]] = {}
    sueltos: list[dict] = []
    for d in docs:
        tipo = d.get("doc_type") or ""
        if tipo in NUNCA_DEDUP:
            # Una estructura PDB o una prediccion NO es un duplicado de un articulo ni de otra
            # estructura que cite el mismo trabajo: cada una es un registro distinto.
            sueltos.append(d)
            continue
        familia = "literatura" if tipo in LITERATURA else tipo
        doi = (d.get("doi") or "").strip().lower()
        clave = f"{familia}|{doi}" if doi else f"{familia}|hash:{d.get('content_hash') or ''}"
        if clave.endswith("|") or clave.endswith("hash:"):
            sueltos.append(d)
        else:
            grupos.setdefault(clave, []).append(d)

    elegidos, absorbidos = list(sueltos), {}
    for clave, grupo in grupos.items():
        grupo = sorted(grupo, key=puntuar, reverse=True)
        ganador, resto = grupo[0], grupo[1:]
        if resto:
            absorbidos[ganador["doc_id"]] = [d["doc_id"] for d in resto]
        elegidos.append(ganador)

    # Segunda pasada: mismo tipo de dato (dataset/software/model) con titulo identico = versiones
    # o espejos del mismo registro (p. ej. HyDB, 13 copias). Gana el de Zenodo (el original) y,
    # si empatan, el de resumen mas largo. Titulos cortos o genericos no se colapsan.
    por_titulo: dict[tuple, list[dict]] = {}
    resto_final = []
    for d in elegidos:
        titulo = re.sub(r"[^a-z0-9]+", " ", (d.get("title") or "").lower()).strip()
        if (d.get("doc_type") or "") in DATASET_TIPOS and len(titulo) >= 25:
            por_titulo.setdefault((d["doc_type"], titulo), []).append(d)
        else:
            resto_final.append(d)
    for grupo in por_titulo.values():
        grupo = sorted(grupo, key=lambda x: (x.get("source") == "zenodo", len(x.get("abstract") or "")),
                       reverse=True)
        ganador, resto = grupo[0], grupo[1:]
        for r in resto:
            absorbidos.setdefault(ganador["doc_id"], []).extend(
                [r["doc_id"], *absorbidos.pop(r["doc_id"], [])])
        resto_final.append(ganador)
    return resto_final, absorbidos


# --- Lectura ----------------------------------------------------------------

COLUMNAS = ["doc_id", "source", "source_id", "doc_type", "title", "authors", "year", "doi",
            "url", "license", "is_open_access", "abstract", "full_text", "raw_path",
            "metadata", "content_hash", "query", "fetched_by"]


def leer_staging(db, limite: int | None) -> list[dict]:
    """Lee staging por paginas: la API de SQL devuelve el resultado en trozos y
    full_text es grande, asi que una sola consulta no trae todas las filas."""
    total = int(db.sql(f"SELECT count(*) FROM {ORIGEN}")[0][0])
    objetivo = min(total, limite) if limite else total
    cols = ", ".join(f"`{c}`" for c in COLUMNAS)
    docs, pagina, offset = [], 200, 0
    while offset < objetivo:
        n = min(pagina, objetivo - offset)
        filas = db.sql(f"SELECT {cols} FROM {ORIGEN} ORDER BY doc_id LIMIT {n} OFFSET {offset}")
        if not filas:
            break
        for f in filas:
            d = dict(zip(COLUMNAS, f))
            d["year"] = int(d["year"]) if d.get("year") and str(d["year"]).isdigit() else None
            docs.append(d)
        offset += len(filas)
        print(f"   leidas {len(docs)} de {objetivo} filas", end="\r")
    print(f"   leidas {len(docs)} de {objetivo} filas")
    return docs


# --- Pipeline ---------------------------------------------------------------

def curar(docs: list[dict]) -> tuple[list[dict], dict]:
    informe = {"filas_staging": len(docs), "descartes": Counter(), "trozos_descartados": Counter(), "relevancia": Counter(), "idioma": Counter(),
               "licencias": Counter(), "anos_sospechosos": 0}

    excluidos = [d for d in docs if EXCLUIR_TITULO.search(d.get("title") or "")]
    ids_excl = {d["doc_id"] for d in excluidos}
    informe["excluidos_manual"] = {"criterio": EXCLUIR_TITULO.pattern, "motivo": "ajeno al dominio",
                                   "docs": len(excluidos), "doc_ids": sorted(ids_excl)}
    docs = [d for d in docs if d["doc_id"] not in ids_excl]

    elegidos, absorbidos = deduplicar(docs)
    informe["duplicados_absorbidos"] = sum(len(v) for v in absorbidos.values())
    informe["absorbidos_por_fuente"] = dict(Counter(a.split(":")[0] for v in absorbidos.values() for a in v))
    informe["docs_tras_dedup"] = len(elegidos)

    ahora = datetime.now(timezone.utc)
    chunks: list[dict] = []
    for d in elegidos:
        resumen, texto = d.get("abstract") or "", d.get("full_text") or ""
        util = len(resumen) >= MIN_RESUMEN or (texto and not RELLENO.search(texto))
        if not util:
            informe["descartes"]["sin_texto_aprovechable"] += 1
            continue

        etiqueta_texto = relevancia(d.get("title", ""), resumen, texto)
        etiqueta = relevancia_doc(d.get("doc_type") or "", d.get("title", ""), resumen, texto)
        licencia = normalizar_licencia(d.get("license", ""))
        ano = d.get("year")
        sospechoso = bool(ano and ano > ahora.year)
        informe["anos_sospechosos"] += int(sospechoso)

        partes = trocear(d)
        if not partes:
            informe["descartes"]["sin_trozos"] += 1
            continue
        idioma = ("en" if (d.get("doc_type") or "") in NUNCA_DEDUP
                  else detectar_idioma(f"{d.get('title') or ''} {resumen or texto[:1500]}"))
        informe["idioma"][idioma] += 1
        informe["relevancia"][etiqueta] += 1
        informe["licencias"][licencia] += 1

        try:
            meta_origen = json.loads(d.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta_origen = {}
        for i, (seccion, texto_trozo, motivo) in enumerate(partes):
            if motivo:
                informe["trozos_descartados"][motivo] += 1
                continue
            texto_trozo = limpiar_html(texto_trozo)
            meta = {"relevancia": etiqueta, "relevancia_texto": etiqueta_texto, "idioma": idioma,
                    "licencia_declarada": d.get("license") or None,
                    "tokens_estimados": tokens(texto_trozo),
                    "es_tabla": parece_tabla(texto_trozo.strip()),
                    "raw_path": d.get("raw_path"), "content_hash": d.get("content_hash")}
            if absorbidos.get(d["doc_id"]):
                meta["duplicate_of"] = absorbidos[d["doc_id"]]
            if sospechoso:
                meta["ano_sospechoso"] = ano
            for k in ("pmid", "pmcid", "journal", "mutations", "uniprot"):
                if meta_origen.get(k):
                    meta[k] = meta_origen[k]
            chunks.append({
                "chunk_id": f"{d['doc_id']}#{i}", "doc_id": d["doc_id"],
                "source": d.get("source"), "doc_type": d.get("doc_type"),
                "title": limpiar_html(d.get("title")), "authors": d.get("authors"), "year": ano,
                "doi": d.get("doi"), "url": d.get("url"), "license": licencia,
                "section": seccion, "text": texto_trozo,
                "relevancia": etiqueta, "idioma": idioma,
                "metadata": json.dumps(meta, ensure_ascii=False),
            })

    informe["docs_curados"] = len({c["doc_id"] for c in chunks})
    informe["chunks"] = len(chunks)
    informe["chunks_tabla"] = sum(json.loads(c["metadata"])["es_tabla"] for c in chunks)
    metas = [json.loads(c["metadata"]) for c in chunks]
    grandes = [m for m in metas if m["tokens_estimados"] > MAX_TOKENS]
    informe["chunks_sobre_maximo"] = {"tablas": sum(m["es_tabla"] for m in grandes),
                                      "texto": sum(not m["es_tabla"] for m in grandes)}
    tk = sorted(m["tokens_estimados"] for m in metas)
    if tk:
        informe["tokens_por_chunk"] = {"mediana": tk[len(tk) // 2], "p90": tk[int(len(tk) * .9)],
                                       "max": tk[-1]}
    informe["descartes"] = dict(informe["descartes"])
    informe["trozos_descartados"] = dict(informe["trozos_descartados"])
    informe["relevancia"] = dict(informe["relevancia"])
    informe["idioma"] = dict(informe["idioma"])
    informe["licencias"] = dict(informe["licencias"].most_common(20))
    return chunks, informe


def _lista_autores(valor) -> list[str]:
    """La API de SQL devuelve ARRAY<STRING> como texto JSON: se decodifica una vez."""
    if isinstance(valor, list):
        return [str(a) for a in valor]
    if isinstance(valor, str) and valor.strip().startswith("["):
        try:
            return [str(a) for a in json.loads(valor)]
        except json.JSONDecodeError:
            pass
    return [valor] if valor else []


def publicar(db, chunks: list[dict], aprobador: str) -> None:
    """Escribe en documents_curated. Solo se llama con --aprobar.

    JSON Lines en el Volume y un unico MERGE por chunk_id: se puede repetir sin
    duplicar (en Databricks la clave primaria es informativa, no se hace cumplir)
    y no depende de vistas temporales, que no sobreviven entre llamadas a la API.
    """
    import io

    ruta = "/Volumes/workspace/lab/raw/curation/documents_curated.jsonl"
    lineas = []
    for c in chunks:
        fila = {k: c.get(k) for k in ("chunk_id", "doc_id", "source", "doc_type", "title",
                                      "year", "doi", "url", "license", "section", "text",
                                      "metadata")}
        fila["authors"] = _lista_autores(c.get("authors"))
        lineas.append(json.dumps(fila, ensure_ascii=False))
    datos = ("\n".join(lineas) + "\n").encode()
    db.w.files.upload(ruta, io.BytesIO(datos), overwrite=True)
    print(f"   subido {len(datos) / 1e6:.1f} MB a {ruta}")

    db.sql(f"""
        MERGE INTO {DESTINO} AS d
        USING (
          SELECT chunk_id, doc_id, source, doc_type, title, authors,
                 CAST(year AS INT) AS year, doi, url, license, section, text, metadata
          FROM read_files('{ruta}', format => 'json',
                          schemaHints => 'authors ARRAY<STRING>, year BIGINT')
        ) AS n
        ON d.chunk_id = n.chunk_id
        WHEN MATCHED THEN UPDATE SET
          doc_id = n.doc_id, source = n.source, doc_type = n.doc_type, title = n.title,
          authors = n.authors, year = n.year, doi = n.doi, url = n.url, license = n.license,
          section = n.section, text = n.text, metadata = n.metadata,
          approved_by = :aprobador, approved_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT
          (chunk_id, doc_id, source, doc_type, title, authors, year, doi, url, license,
           section, text, metadata, approved_by, approved_at)
          VALUES (n.chunk_id, n.doc_id, n.source, n.doc_type, n.title, n.authors, n.year,
                  n.doi, n.url, n.license, n.section, n.text, n.metadata,
                  :aprobador, current_timestamp())""", params={"aprobador": aprobador})
    n = db.sql(f"SELECT count(*), count(DISTINCT doc_id) FROM {DESTINO}")[0]
    print(f"   {DESTINO}: {n[0]} chunks de {n[1]} documentos")
    # Regla del repo: cada decision de curacion relevante queda registrada y se puede reconstruir.
    from data_pipeline.curation.registro import registrar_aprobacion
    registrar_aprobacion(db, aprobador, f"{len(chunks)} chunks de {len({c['doc_id'] for c in chunks})} "
                         "documentos aprobados para documents_curated",
                         sorted({c["doc_id"] for c in chunks})[:200], {"chunks": len(chunks)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limite", type=int, help="procesa solo N filas de staging (prueba)")
    ap.add_argument("--aprobar", metavar="NOMBRE",
                    help="publica en documents_curated con ese approved_by. "
                         "Sin esta opcion no se escribe nada en Databricks.")
    ap.add_argument("--salida", default=str(SALIDA))
    args = ap.parse_args()

    if args.aprobar and args.limite:
        raise SystemExit("--aprobar con --limite publicaria solo una parte del corpus: "
                         "quita --limite para publicar.")
    from data_pipeline.databricks_io import Databricks
    db = Databricks()

    print(f"Leyendo {ORIGEN}...")
    docs = leer_staging(db, args.limite)
    chunks, informe = curar(docs)

    print("\n=== Propuesta de curacion ===")
    print(f"  filas en staging:        {informe['filas_staging']}")
    print(f"  duplicados absorbidos:  -{informe['duplicados_absorbidos']} {informe['absorbidos_por_fuente']}")
    print(f"  descartados:            -{sum(informe['descartes'].values())} {informe['descartes']}")
    print(f"  documentos curados:      {informe['docs_curados']}")
    print(f"  trozos descartados:      {informe['trozos_descartados']}")
    print(f"  chunks:                  {informe['chunks']} "
          f"({informe['chunks_tabla']} son tablas enteras)")
    print(f"  tokens por chunk:        {informe.get('tokens_por_chunk')}")
    print(f"  sobre {MAX_TOKENS} tokens:        {informe['chunks_sobre_maximo']} "
          f"(las tablas se dejan enteras a proposito)")
    print(f"  relevancia:              {informe['relevancia']}")
    print(f"  idioma:                  {informe['idioma']}")
    print(f"  excluidos a mano:        {informe['excluidos_manual']['docs']} ({informe['excluidos_manual']['motivo']})")
    print(f"  anos posteriores a hoy:  {informe['anos_sospechosos']} (marcados, no borrados)")
    print(f"  licencias:               {informe['licencias']}")

    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    (out / "informe.json").write_text(json.dumps(informe, indent=2, ensure_ascii=False))
    with (out / "chunks.jsonl").open("w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Todos los chunks: {out / 'chunks.jsonl'}")
    muestra = [{k: v for k, v in c.items() if k != "authors"} for c in chunks[:200]]
    (out / "muestra_chunks.json").write_text(json.dumps(muestra, indent=2, ensure_ascii=False))
    print(f"\nInforme: {out / 'informe.json'}")
    print(f"Muestra de 200 chunks: {out / 'muestra_chunks.json'}")

    if args.aprobar:
        print(f"\nPublicando en {DESTINO} como '{args.aprobar}'...")
        publicar(db, chunks, args.aprobar)
    else:
        print("\nNO se escribio en Databricks (es una propuesta). Revisa la muestra y, "
              "si esta bien, vuelve a correr con --aprobar \"Tu Nombre\".")


if __name__ == "__main__":
    main()
