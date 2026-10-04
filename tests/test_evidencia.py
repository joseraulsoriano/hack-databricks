"""Pasajes para el agente. Sin red: `_sql` se sustituye.

Lo que se fija aqui: que el `evidence_span` salga LITERAL (si se recorta o se
limpia, la puerta lo rechaza y el agente no puede construir nada), que el respaldo
venga de varias fuentes y que se pueda buscar dentro de un solo documento.
"""

import unittest

from ar_vr_bridge import evidencia

# chunk_id, doc_id, source, title, year, doi, url, section, text
FILAS = [
    ["c1", "europepmc:1", "europepmc", "Thermostable PETase", 2024, "10.1/a", "u1", "Results",
     "The engineered variant showed enhanced thermostability at 60 degrees without losing activity."],
    ["c2", "europepmc:1", "europepmc", "Thermostable PETase", 2024, "10.1/a", "u1", "Methods",
     "Plasmids were constructed following a standard protocol described elsewhere in detail."],
    ["c3", "openalex:W2", "openalex", "Engineering PET hydrolases", 2023, "10.1/b", "u2", "Introduction",
     "Protein engineering of PET hydrolases improved thermostability in several reported studies."],
    ["c4", "europepmc:1", "europepmc", "Thermostable PETase", 2024, "10.1/a", "u1", "Discussion",
     "Thermostability gains were confirmed by differential scanning calorimetry measurements here."],
    ["c5", "europepmc:1", "europepmc", "Thermostable PETase", 2024, "10.1/a", "u1", "Results",
     "An additional thermostability observation unrelated to the main engineering claim follows."],
]


class Pasajes(unittest.TestCase):
    def setUp(self):
        self.orig = evidencia._sql
        evidencia._sql = lambda s, params=None: list(FILAS)

    def tearDown(self):
        evidencia._sql = self.orig

    def test_el_span_sale_literal_para_poder_pegarlo(self):
        # Si se recortara o se limpiara, la puerta lo rechazaria por span_literal.
        p = evidencia.buscar("thermostability", 5)
        self.assertIn(FILAS[0][8], [x["evidence_span"] for x in p])

    def test_un_trozo_sin_relacion_no_entra(self):
        p = evidencia.buscar("thermostability", 10)
        self.assertNotIn("c2", [x["chunk_id"] for x in p], "Methods no comparte terminos")

    def test_el_respaldo_viene_de_varias_fuentes(self):
        p = evidencia.buscar("thermostability engineering", 10)
        self.assertGreaterEqual(len({x["doc_id"] for x in p}), 2)

    def test_un_solo_documento_no_inunda_la_respuesta(self):
        p = evidencia.buscar("thermostability", 10)
        de_uno = [x for x in p if x["doc_id"] == "europepmc:1"]
        self.assertLessEqual(len(de_uno), evidencia.MAXIMO_POR_DOC)

    def test_se_puede_buscar_dentro_de_un_documento(self):
        # El equivalente a "mira en este paper esta parte".
        p = evidencia.buscar("thermostability", 10, doc_id="europepmc:1")
        self.assertTrue(p)
        self.assertEqual({x["doc_id"] for x in p}, {"europepmc:1"})

    def test_se_respeta_num_results(self):
        self.assertEqual(len(evidencia.buscar("thermostability", 2)), 2)

    def test_los_mas_solapados_van_primero(self):
        p = evidencia.buscar("thermostability engineering", 10)
        self.assertGreaterEqual(p[0]["overlap"], p[-1]["overlap"])


class Documento(unittest.TestCase):
    def setUp(self):
        self.orig = evidencia._sql
        evidencia._sql = lambda s, params=None: [f for f in FILAS if f[1] == "europepmc:1"]

    def tearDown(self):
        evidencia._sql = self.orig

    def test_devuelve_el_documento_entero_con_sus_secciones(self):
        d = evidencia.documento("europepmc:1")
        self.assertEqual(d["chunks"], 4)
        self.assertEqual(d["sections"], ["Discussion", "Methods", "Results"])
        self.assertEqual(d["title"], "Thermostable PETase")

    def test_un_documento_que_no_existe_devuelve_vacio(self):
        evidencia._sql = lambda s, params=None: []
        self.assertEqual(evidencia.documento("europepmc:999"), {})


class Endpoints(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from ar_vr_bridge import retrieval
        from ar_vr_bridge.app import app
        self.orig = (evidencia._sql, retrieval.search)
        evidencia._sql = lambda s, params=None: list(FILAS)
        retrieval.search = lambda q, n, **k: ([], False, 10)   # el RAG no decide aqui
        self.cliente = TestClient(app)

    def tearDown(self):
        from ar_vr_bridge import retrieval
        evidencia._sql, retrieval.search = self.orig

    def test_evidence_devuelve_pasajes_y_las_fuentes(self):
        b = self.cliente.post("/api/v1/evidence",
                              json={"query": "thermostability engineering", "num_results": 5}).json()
        self.assertGreater(b["count"], 0)
        self.assertGreaterEqual(len(b["doc_ids"]), 2)
        self.assertTrue(all(p["evidence_span"] for p in b["passages"]))

    def test_evidence_valida_la_consulta(self):
        self.assertEqual(self.cliente.post("/api/v1/evidence", json={"query": ""}).status_code, 422)

    def test_documento_inexistente_es_404(self):
        evidencia._sql = lambda s, params=None: []
        self.assertEqual(self.cliente.get("/api/v1/documents/europepmc:999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
