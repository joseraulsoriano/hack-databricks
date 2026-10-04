-- workspace.lab: tablas del laboratorio de descubrimiento (PETasa).
-- Flujo: conectores -> raw (Volume) -> documents_staging -> [aprobación humana] -> documents_curated -> índice AI Search.

-- Lo que traen los conectores o el agente. Sin deduplicar ni limpiar: eso es trabajo de curación.
CREATE TABLE IF NOT EXISTS workspace.lab.documents_staging (
  doc_id         STRING    COMMENT 'Clave estable: <source>:<source_id>',
  source         STRING    COMMENT 'europepmc | openalex | pdb | alphafold | zenodo | manual',
  source_id      STRING    COMMENT 'ID nativo de la fuente (PMID, W..., 5XJH, accesión UniProt, record Zenodo)',
  doc_type       STRING    COMMENT 'article | thesis | preprint | structure | prediction | dataset',
  title          STRING,
  authors        ARRAY<STRING>,
  year           INT,
  doi            STRING    COMMENT 'Minúsculas, sin prefijo https://doi.org/',
  url            STRING,
  license        STRING    COMMENT 'Licencia declarada por la fuente; vacío si no consta',
  is_open_access BOOLEAN,
  abstract       STRING,
  full_text      STRING    COMMENT 'Texto completo cuando la fuente lo ofrece (Europe PMC OA)',
  raw_path       STRING    COMMENT 'Ruta del payload original en /Volumes/workspace/lab/raw',
  metadata       STRING    COMMENT 'JSON con campos específicos de la fuente',
  content_hash   STRING    COMMENT 'sha256 de title+abstract+full_text, útil para deduplicar',
  query          STRING    COMMENT 'Consulta que lo trajo',
  fetched_by     STRING    COMMENT 'ingest_cli | agent:<nombre>',
  fetched_at     TIMESTAMP
) COMMENT 'Documentos propuestos por conectores o agentes, pendientes de curación';

-- Lo aprobado por la persona curadora. CDF activo para el índice Delta Sync de AI Search.
CREATE TABLE IF NOT EXISTS workspace.lab.documents_curated (
  chunk_id     STRING NOT NULL COMMENT '<doc_id>#<n>; clave primaria del índice',
  doc_id       STRING,
  source       STRING,
  doc_type     STRING,
  title        STRING,
  authors      ARRAY<STRING>,
  year         INT,
  doi          STRING,
  url          STRING,
  license      STRING,
  section      STRING,
  text         STRING    COMMENT 'Texto limpio a indexar',
  metadata     STRING,
  approved_by  STRING,
  approved_at  TIMESTAMP,
  -- Etiquetas por trozo (ver docs/SUBTEMAS.md). En un workspace que ya tiene la tabla se anaden
  -- con data_pipeline/curation/migrar_esquema.py: editar este CREATE no modifica una tabla existente.
  relevance          STRING COMMENT 'Nivel de relevancia del documento (titulo + resumen); filtro por defecto del RAG',
  language           STRING COMMENT 'Idioma detectado por heuristica: en, es, pt, de, fr, cjk, cirilico, desconocido',
  subtopic           STRING COMMENT 'Subtema principal del trozo (7 aprobados) o NULL si ninguno llega al umbral',
  subtopic_secondary STRING COMMENT 'Segundo subtema si pesa casi como el primero, o NULL',
  evidence_type      STRING COMMENT 'Tipo de evidencia derivado de la seccion: resumen, tabla, metodos, resultados...',
  enzyme             ARRAY<STRING> COMMENT 'Enzimas especificas nombradas en el trozo (no incluye PETase generica)',
  CONSTRAINT documents_curated_pk PRIMARY KEY (chunk_id)
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
  COMMENT 'Corpus aprobado y troceado; fuente del índice de AI Search';

-- Tabla que el agente extrae de la literatura: variante -> efecto medido, siempre con cita.
CREATE TABLE IF NOT EXISTS workspace.lab.mutant_stability (
  record_id     STRING,
  enzyme        STRING    COMMENT 'IsPETase, ThermoPETase, FAST-PETase, ...',
  uniprot       STRING,
  mutations     STRING    COMMENT 'Notación estándar separada por comas, p. ej. S121E,D186H',
  metric        STRING    COMMENT 'Tm | dTm | activity | half_life | ...',
  value         DOUBLE,
  unit          STRING,
  conditions    STRING,
  doc_id        STRING    COMMENT 'Documento de origen (cita obligatoria)',
  evidence_span STRING    COMMENT 'Frase o fila de tabla de donde sale el dato',
  extracted_by  STRING    COMMENT 'agent:<nombre> | human:<nombre>',
  verified      BOOLEAN   COMMENT 'Revisado por una persona',
  created_at    TIMESTAMP
) COMMENT 'Estabilidad/actividad de variantes de PETasa extraída con cita';

-- Registro compartido: cada decisión del lab se puede reconstruir. También alimenta el visor de Quest.
CREATE TABLE IF NOT EXISTS workspace.lab.research_record (
  event_id    STRING,
  session_id  STRING,
  ts          TIMESTAMP,
  from_agent  STRING,
  to_agent    STRING,
  kind        STRING    COMMENT 'handoff | tool_call | result | decision | approval_request | approval',
  flow        STRING    COMMENT 'evidence | experiment | safety',
  weight      DOUBLE,
  summary     STRING,
  refs        ARRAY<STRING> COMMENT 'doc_id o record_id citados',
  payload     STRING    COMMENT 'JSON con el detalle'
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
  COMMENT 'Registro de investigación compartido entre agentes';

-- Toda pregunta que entra al lab, por voz o por texto. Contrato: docs/API.md.
-- Se inserta AL RECIBIR con answered = false y se actualiza al terminar: si una
-- pregunta tumba el laboratorio, su fila se queda en answered = false y se puede
-- encontrar. Escribir solo al terminar perderia justo la que mas interesa ver.
CREATE TABLE IF NOT EXISTS workspace.lab.queries (
  query_id         STRING NOT NULL COMMENT 'q_<8 hex>; el mismo id que usa la corrida',
  query            STRING NOT NULL COMMENT 'El texto TAL CUAL llego: las asperezas del transcriptor son evidencia',
  source           STRING NOT NULL COMMENT 'voice | text | agent',
  asked_by         STRING          COMMENT 'Revisor, del ?reviewer= de la gate',
  asked_at         TIMESTAMP NOT NULL,
  mode             STRING NOT NULL COMMENT 'live | mock; para no confundir una corrida simulada con una real',
  language         STRING          COMMENT 'BCP-47 si el cliente lo mando',
  answered         BOOLEAN NOT NULL,
  verdict          STRING          COMMENT 'PASS | WARN | FAIL, de Validation',
  has_evidence     BOOLEAN         COMMENT 'Para /api/v1/ask',
  latency_ms       INT,
  citation_doc_ids ARRAY<STRING>   COMMENT 'Los doc_id realmente citados',
  error            STRING,
  CONSTRAINT queries_pk PRIMARY KEY (query_id)
) COMMENT 'Preguntas recibidas por el puente, con su resultado';
