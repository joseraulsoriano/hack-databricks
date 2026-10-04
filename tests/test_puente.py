"""Puente AR/VR: lo que llega al visor. Sin red ni Databricks (`_query` y `_sql` se
sustituyen). Cada caso fija un fallo real que se midio contra el workspace.
"""

import json
import unittest

from ar_vr_bridge import retrieval

# Filas tal como las devuelve el indice: chunk_id, doc_id, title, year, doi, url, source, score
FILAS = [
    ["c1", "openalex:W4385684008", "Thermostability enhancement", 2023, "10.1/a", "u1", "openalex", 0.841],
    ["c2", "europepmc:35382549", "Engineering thermostable IsPETase", 2022, "10.1/b", "u2", "europepmc", 0.817],
    ["c3", "europepmc:40069109", "Mining thermophile genomes", 2025, "10.1/c", "u3", "europepmc", 0.809],
]
CURADOS = {"europepmc:35382549", "europepmc:40069109"}


class FiltroDeCuracion(unittest.TestCase):
    """879 documentos del indice no estan en documents_curated: no se pueden citar."""

    def setUp(self):
        self.orig = (retrieval._query, retrieval._sql, retrieval._aprobados, retrieval._aprobados_ts)
        retrieval._query = lambda texto, n: list(FILAS)
        retrieval._aprobados, retrieval._aprobados_ts = set(CURADOS), 9e18

    def tearDown(self):
        (retrieval._query, retrieval._sql,
         retrieval._aprobados, retrieval._aprobados_ts) = self.orig

    def test_un_documento_sin_curar_no_se_cita_aunque_puntue_mas_alto(self):
        citas, _, _ = retrieval.search("thermostability", 5)
        ids = [c.doc_id for c in citas]
        self.assertNotIn("openalex:W4385684008", ids)   # 0.841, el mejor, pero sin curar
        self.assertEqual(ids, ["europepmc:35382549", "europepmc:40069109"])

    def test_si_no_se_sabe_que_esta_curado_no_se_deja_mudo_el_visor(self):
        # None significa "no se pudo leer el corpus", no "no hay nada aprobado".
        retrieval._aprobados, retrieval._aprobados_ts = None, 0.0
        retrieval._sql = lambda s: (_ for _ in ()).throw(RuntimeError("warehouse caido"))
        citas, _, _ = retrieval.search("thermostability", 5)
        self.assertEqual(len(citas), 3)

    def test_hay_evidencia_se_juzga_sobre_lo_que_queda_tras_filtrar(self):
        citas, hay, _ = retrieval.search("thermostability", 5)
        self.assertTrue(hay)
        self.assertGreaterEqual(citas[0].score, retrieval.SCORE_THRESHOLD)


class Snippet(unittest.TestCase):
    """El visor del equipo muestra `snippet` como la frase que sostiene la cita."""

    def setUp(self):
        self.orig = (retrieval._query, retrieval._sql, retrieval._aprobados, retrieval._aprobados_ts)
        retrieval._query = lambda texto, n: list(FILAS)
        retrieval._aprobados, retrieval._aprobados_ts = set(CURADOS), 9e18
        retrieval._sql = lambda s: [
            ["europepmc:35382549", "Engineering thermostable IsPETase variants for degradation."],
            ["europepmc:40069109", "Un texto sin relacion alguna con la consulta pedida."],
        ]

    def tearDown(self):
        (retrieval._query, retrieval._sql,
         retrieval._aprobados, retrieval._aprobados_ts) = self.orig

    def test_se_elige_el_trozo_que_comparte_terminos_con_la_consulta(self):
        citas, _, _ = retrieval.search("thermostable degradation", 5, con_snippet=True)
        por_id = {c.doc_id: c.snippet for c in citas}
        self.assertIn("thermostable", por_id["europepmc:35382549"].lower())

    def test_sin_solape_el_snippet_queda_vacio_en_vez_de_insinuar(self):
        # Preferimos vacio a un fragmento que parezca la evidencia sin serlo.
        citas, _, _ = retrieval.search("thermostable degradation", 5, con_snippet=True)
        por_id = {c.doc_id: c.snippet for c in citas}
        self.assertEqual(por_id["europepmc:40069109"], "")

    def test_el_camino_de_voz_no_paga_la_consulta_de_snippet(self):
        llamadas = []
        retrieval._sql = lambda s: llamadas.append(s) or []
        retrieval.search("thermostable", 5)            # con_snippet=False por defecto
        self.assertEqual(llamadas, [])


class EventosDelVisor(unittest.TestCase):
    """`_events`: la caida a simulador se anuncia y la latencia se mide."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from ar_vr_bridge.app import app
        self.cliente = TestClient(app)

    def _correr(self, mode):
        with self.cliente.websocket_connect("/ws/explore") as ws:
            ws.send_text(json.dumps({"query": "termoestabilidad", "mode": mode}))
            eventos = []
            while True:
                e = ws.receive_json()
                eventos.append(e)
                if e["event"] == "done":
                    return eventos

    def test_pedir_live_sin_orquestador_lo_dice_en_vez_de_fingir(self):
        eventos = self._correr("live")
        avisos = [e for e in eventos if e["event"] == "stage" and "simulador" in e.get("message", "")]
        self.assertTrue(avisos, "el visor debe saber que no esta hablando con el laboratorio")

    def test_la_latencia_del_done_es_la_medida_no_la_declarada(self):
        # Traia 9000 ms fijos mientras el reloj real marcaba 13 600.
        eventos = self._correr("mock")
        done = eventos[-1]
        self.assertNotEqual(done["latency_ms"], 9000)
        self.assertGreater(done["latency_ms"], 0)


if __name__ == "__main__":
    unittest.main()
