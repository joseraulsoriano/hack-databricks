"""Etiquetas por trozo: subtema (principal y secundario), tipo de evidencia y enzimas.

SUBTEMAS: los 7 de la tabla aprobada, obtenidos explorando el corpus (explorar_subtemas.py).
La asignacion es por TROZO, no por documento: el 80 % de los documentos toca tres o mas temas,
y un trozo es mucho mas especifico que el articulo que lo contiene.

COMO SE ASIGNA (determinista y auditable, no un modelo)
  Cada subtema tiene un lexico de patrones con peso. La puntuacion de un trozo es la suma, por
  patron, de min(apariciones, 3) x peso, y el nombre de la seccion cuenta doble. Se asigna
  - subtopic: el subtema de mayor puntuacion si llega a UMBRAL;
  - subtopic_secondary: el segundo si tambien llega a UMBRAL y a SECUNDARIO x la del primero.
  Si ningun subtema llega al umbral el trozo queda sin subtema (NULL): es lo correcto para
  metodos genericos, agradecimientos o texto sin tema claro. Un NULL honesto vale mas que una
  etiqueta inventada.

LIMITES: es una heuristica de palabras clave sin verificar contra etiquetas humanas. La coherencia
con el agrupamiento automatico del corpus se mide en explorar_subtemas / informe de curacion.
"""

import re

from data_pipeline.curation.extraer_mutantes import ENZIMA, GENERICAS, canon

SUBTEMAS = {
    "ingenieria_estabilidad": "Ingeniería y estabilidad de enzimas",
    "descubrimiento_caracterizacion": "Descubrimiento y caracterización",
    "reciclaje_economia_circular": "Reciclaje, upcycling y economía circular",
    "biodegradacion_ambiente": "Biodegradación microbiana y microplásticos (ambiente)",
    "produccion_sistemas_celulares": "Producción y sistemas celulares",
    "mecanismo_simulacion": "Mecanismo y simulación",
    "productos_despolimerizacion": "Productos de despolimerización (MHET, TPA, BHET)",
}
UMBRAL = 4            # puntuacion minima para asignar subtema
SECUNDARIO = 0.6      # el segundo debe llegar a esta fraccion del primero

_L = {
    "ingenieria_estabilidad": [
        (r"thermostab\w*|thermal(?:ly)? stab\w*|heat[- ]resist\w*", 2),
        (r"melting temperature|\bT\s?m\b|Δ\s?T\s?m\b", 2),
        (r"directed evolution|rational design|protein engineering|enzyme engineering|computational design|"
         r"machine learning|deep learning|ProteinMPNN|\bPROSS\b|FoldX|Rosetta", 2),
        (r"\bvariants?\b|\bmutants?\b|\bmutations?\b|mutagenesis|substitutions?", 1),
        (r"disulfide|stabili[sz]\w*|ΔΔG|salt bridge", 1),
        (r"FAST-PETase|DuraPETase|ThermoPETase|HotPETase|TurboPETase|LCC-?ICCG|\bICCG\b", 2),
        (r"engineered|improved (?:activity|stability)|enhanced (?:activity|stability)", 1)],
    "descubrimiento_caracterizacion": [
        (r"metagenom\w*|genome mining|bioprospect\w*|uncultured|metatranscriptom\w*", 2),
        (r"novel (?:PET )?(?:hydrolase|enzyme|polyesterase|cutinase|esterase)|newly (?:identified|discovered)", 2),
        (r"\bisolat(?:ed|ion|es)\b|screening|screened|enrichment", 1),
        (r"compost|sediment|sponge|deep[- ]sea|marine|soil sample|hot spring", 1),
        (r"putative|homolog\w*|phylogen\w*|sequence (?:similarity|identity|alignment)|\bHMM\b|BLAST|clade|subfamily", 1),
        (r"discover\w*|identif(?:ied|ication) of", 1),
        (r"(?:biochemical )?characteri[sz]ation of", 1),
        (r"Ideonella|sakaiensis|201-F6|bacterium|bacterial strain", 1)],
    "reciclaje_economia_circular": [
        (r"circular economy|closed[- ]loop|circularity", 2),
        (r"recycl\w*|upcycl\w*|valori[sz]\w*", 2),
        (r"sustainab\w*|waste management|life[- ]cycle|techno-?economic|feedstock|bio-?based|value-added", 1),
        (r"mechanical recycling|chemical recycling|glycolysis|pyrolysis|methanolysis|solvolysis", 2),
        (r"post-?consumer|plastic waste|waste plastic|\bwaste\b", 1),
        (r"industrial(?:ly)?|scale[- ]up|large[- ]scale|commerciali[sz]\w*", 1)],
    "biodegradacion_ambiente": [
        (r"microplastic\w*|nanoplastic\w*|plastisphere", 2),
        (r"pollution|ecosystem|ecolog\w*|marine litter|ocean|aquatic|landfill|bioremediation|"
         r"environmental (?:impact|fate|persistence)", 1),
        (r"biodegrad\w*", 1),
        (r"microbial communit\w*|microbiome|consortium|consortia|biofilm|relative abundance|\b16S\b", 2),
        (r"toxic\w*|human health|exposure|ingestion", 1),
        (r"\bsoil\b|\bsea\b|freshwater|wastewater|sludge", 1)],
    "produccion_sistemas_celulares": [
        (r"surface display|cell[- ]surface|yeast display|whole[- ]cell|biocatalyst\w*", 2),
        (r"heterologous|recombinant|expression (?:system|host|vector|level)|overexpress\w*|\bexpressed\b", 1),
        (r"E\. coli|Escherichia coli|Pichia|Saccharomyces|Bacillus subtilis|Corynebacterium|Yarrowia|"
         r"chloroplast|microalga\w*|chassis", 1),
        (r"secretion|signal peptide|fusion protein|inclusion bod\w*|His-?tag|purif(?:ied|ication)|Ni-?NTA|"
         r"affinity chromatography", 1),
        (r"immobili[sz]\w*|fermentation|bioreactor|fed-batch|titer", 2),
        (r"plasmid|cloning|transformant|induction|IPTG|OD600", 1)],
    "mecanismo_simulacion": [
        (r"molecular dynamics|\bMD simulations?\b|QM/MM|\bDFT\b|density functional|docking|free[- ]energy|"
         r"kcal/?\s?mol|GROMACS|AMBER|CHARMM|metadynamics", 2),
        (r"catalytic (?:triad|mechanism|residue|site|cycle)|oxyanion|tetrahedral intermediate|acyl-?enzyme|"
         r"nucleophilic attack", 2),
        (r"active site|binding (?:site|affinity|energy|pocket|cleft|mode)|substrate[- ]binding|subsite", 1),
        (r"crystal structure|X-?ray|cryo-?EM|\bPDB\b", 1),
        (r"conformation\w*|\bRMSD\b|\bRMSF\b|hydrogen bond|electrostatic|hydrophobic", 1),
        (r"mechanis(?:m|tic) (?:insight|study|investigation)", 1)],
    "productos_despolimerizacion": [
        (r"terephthalic acid|\bTPA\b|\bMHET\b|\bBHET\b|mono\(2-hydroxyethyl\)|bis\(2-hydroxyethyl\)|MHETase", 3),
        (r"ethylene glycol|\bEG\b", 1),
        # "monomer", "depolymerization" o "HPLC" salen en casi todo articulo de PET: peso bajo,
        # y las dos ultimas de las tres se dejan fuera de la suma salvo junto a un producto concreto.
        (r"monomer\w*|oligomer\w*|degradation products?|hydrolysis products?|soluble products?|product release", 1),
        (r"depolymeri[sz]\w*|PET hydrolysis", 1)],
}
LEXICO = {k: [(re.compile(p, re.I), w) for p, w in v] for k, v in _L.items()}


def puntuar(texto: str, seccion: str = "") -> dict[str, int]:
    out = {}
    for sub, patrones in LEXICO.items():
        total = 0
        for rx, peso in patrones:
            total += min(len(rx.findall(texto)), 3) * peso
            if seccion and rx.search(seccion):
                total += 2 * peso                      # el titulo de la seccion cuenta doble
        out[sub] = total
    return out


def asignar_subtemas(texto: str, seccion: str = "", umbral: int = UMBRAL) -> tuple[str | None, str | None]:
    pts = puntuar(texto, seccion)
    orden = sorted(pts.items(), key=lambda kv: -kv[1])
    (s1, p1), (s2, p2) = orden[0], orden[1]
    if p1 < umbral:
        return None, None
    return s1, (s2 if p2 >= umbral and p2 >= SECUNDARIO * p1 else None)


# --- Tipo de evidencia: deriva SOLO del nombre de la seccion y del tipo de documento ---------

_EV = [
    ("introduccion", re.compile(r"(?i)^(?:\d+[.)]?\s*)?(?:introduction|background|overview|state of the art)")),
    ("metodos", re.compile(r"(?i)method|material|experimental|procedure|protocol|preparation|cloning|purification|"
                           r"\bstrains?\b|plasmids?|culture|growth conditions|statistical|data analysis|assays?\b|"
                           r"measurements?$|samples?\b|computational details|simulation (?:setup|details)")),
    ("resultados", re.compile(r"(?i)result|finding|characteri[sz]ation|screening|identification|"
                              r"activity|stability|kinetic|structur|simulation")),
    ("discusion", re.compile(r"(?i)discussion|conclusion|outlook|perspective|future|summary|challenges|"
                             r"limitations|implications|concluding")),
]


def tipo_evidencia(seccion: str, doc_type: str, es_tabla: bool) -> str:
    """Vocabulario cerrado: resumen, tabla, estructura, prediccion, introduccion, metodos,
    resultados, discusion, otro. Es una pista por la seccion, no un analisis del contenido."""
    if doc_type == "structure":
        return "estructura"
    if doc_type == "prediction":
        return "prediccion"
    if es_tabla:
        return "tabla"
    if seccion == "Resumen":
        return "resumen"
    for nombre, rx in _EV:
        if rx.search(seccion or ""):
            return nombre
    return "otro"


def enzimas(texto: str, maximo: int = 8) -> list[str]:
    """Nombres de enzima especificos que aparecen en el trozo (se omiten 'PETase', 'cutinase'...)."""
    vistos = dict.fromkeys(
        canon(m.group(0)) for m in ENZIMA.finditer(texto)
        if canon(m.group(0)).lower() not in GENERICAS)
    return list(vistos)[:maximo]


def etiquetar_trozo(texto: str, seccion: str, doc_type: str, es_tabla: bool) -> dict:
    sub, sec = (None, None) if doc_type in ("structure", "prediction") else asignar_subtemas(texto, seccion)
    return {"subtopic": sub, "subtopic_secondary": sec,
            "evidence_type": tipo_evidencia(seccion, doc_type, es_tabla),
            "enzyme": enzimas(texto)}
