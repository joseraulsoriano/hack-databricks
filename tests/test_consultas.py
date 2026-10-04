"""Persistencia de preguntas y cumplimiento del contrato de /api/v1/ask.
Sin red: `insertar`, `completar` y el RAG se sustituyen.
"""

import asyncio
import os
import time
import unittest

from ar_vr_bridge import consultas, redaccion, retrieval
from ar_vr_bridge.contract import Citation


def cita(doc_id="europepmc:1", snippet="", score=0.83, titulo="Un titulo", autores="Joo et al."):
    return Citation(id="c1", doc_id=doc_id, title=titulo, authors_short=autores,
                    year=2018, snippet=snippet, score=score)


class OrdenDeEscritura(unittest.IsolatedAsyncioTestCase):
    """Se inserta al recibir; el update no puede adelantarse al insert."""

    def setUp(self):
        self.orig = (consultas.insertar, consultas.completar)
        self.eventos = []
        consultas._llegadas.clear()
        self.env = os.environ.get("QUERIES_LOG")
        os.environ["QUERIES_LOG"] = "1"   # la prueba mide el camino activo

    def tearDown(self):
        consultas.insertar, consultas.completar = self.orig
        if self.env is None:
            os.environ.pop("QUERIES_LOG", None)
        else:
            os.environ["QUERIES_LOG"] = self.env

    async def _vaciar(self):
        while consultas._en_vuelo:
            await asyncio.gather(*list(consultas._en_vuelo), return_exceptions=True)

    async def test_el_insert_ocurre_antes_que_el_update(self):
        def ins(*a, **k):
            time.sleep(0.05)              # el insert tarda; corre en un hilo
            self.eventos.append("insert")
        consultas.insertar = ins
        consultas.completar = lambda *a, **k: self.eventos.append("update")

        consultas.llegada("q_1", "hola", "voice", "live")
        consultas.final("q_1", latency_ms=10)
        await self._vaciar()
        self.assertEqual(self.eventos, ["insert", "update"])

    async def test_una_corrida_que_falla_deja_la_fila_sin_responder(self):
        cerrado = {}
        consultas.insertar = lambda *a, **k: None
        consultas.completar = lambda qid, **k: cerrado.update(k)
        consultas.llegada("q_2", "hola", "voice", "live")
        consultas.final("q_2", error="se cayo el lab")
        await self._vaciar()
        self.assertEqual(cerrado["error"], "se cayo el lab")

    async def test_si_databricks_falla_no_se_propaga(self):
        def caido(*a, **k):
            raise RuntimeError("warehouse apagado")
        consultas.insertar = caido
        consultas.llegada("q_3", "hola", "voice", "live")
        await self._vaciar()


class ContratoAsk(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from ar_vr_bridge.app import app
        self.orig = (retrieval.search, consultas.llegada, consultas.final)
        consultas.llegada = lambda *a, **k: None
        consultas.final = lambda *a, **k: None
        self.cliente = TestClient(app)

    def tearDown(self):
        retrieval.search, consultas.llegada, consultas.final = self.orig

    def test_sin_evidencia_no_se_devuelve_ni_una_cita(self):
        # La regla que separa esto de un chatbot: el visor no debe poder pintar
        # fuentes que no sostienen nada.
        retrieval.search = lambda q, n, **k: ([cita(score=0.41)], False, 120)
        b = self.cliente.post("/api/v1/ask", json={"query": "precio del billete a la luna"}).json()
        self.assertFalse(b["has_evidence"])
        self.assertEqual(b["citations"], [])
        self.assertIn("No encuentro evidencia", b["answer"])

    def test_con_evidencia_el_tts_no_pasa_de_40_palabras(self):
        largo = " ".join(f"palabra{i}" for i in range(200)) + "."
        retrieval.search = lambda q, n, **k: ([cita(snippet=largo)], True, 120)
        b = self.cliente.post("/api/v1/ask", json={"query": "termoestabilidad"}).json()
        self.assertTrue(b["has_evidence"])
        self.assertLessEqual(len(b["tts_text"].split()), redaccion.TTS_MAX_PALABRAS)
        self.assertTrue(b["tts_text"])

    def test_se_respeta_el_query_id_del_cliente(self):
        retrieval.search = lambda q, n, **k: ([cita(snippet="algo")], True, 10)
        b = self.cliente.post("/api/v1/ask",
                              json={"query": "x", "query_id": "q_abc12345"}).json()
        self.assertEqual(b["query_id"], "q_abc12345")

    def test_la_respuesta_trae_schema_version(self):
        retrieval.search = lambda q, n, **k: ([cita(snippet="algo")], True, 10)
        b = self.cliente.post("/api/v1/ask", json={"query": "x"}).json()
        self.assertEqual(b["schema_version"], "1.0")

    def test_una_consulta_vacia_es_422(self):
        self.assertEqual(self.cliente.post("/api/v1/ask", json={"query": ""}).status_code, 422)

    def test_num_results_fuera_de_rango_es_422(self):
        self.assertEqual(
            self.cliente.post("/api/v1/ask", json={"query": "x", "num_results": 50}).status_code, 422)

    def test_si_el_indice_no_responde_es_503(self):
        def caido(*a, **k):
            raise RuntimeError("indice inalcanzable")
        retrieval.search = caido
        self.assertEqual(self.cliente.post("/api/v1/ask", json={"query": "x"}).status_code, 503)


class Redaccion(unittest.TestCase):
    def test_la_respuesta_es_el_pasaje_citado_no_prosa_inventada(self):
        pasaje = "The narrow active site cleft accommodates the aromatic substrate."
        answer, _ = redaccion.redactar([cita(snippet=pasaje)])
        self.assertIn(pasaje, answer)
        self.assertIn("europepmc:1", answer)

    def test_sin_pasaje_se_nombra_la_fuente_sin_inventar_una_frase(self):
        answer, tts = redaccion.redactar([cita(snippet="")])
        self.assertIn("Un titulo", answer)
        self.assertLessEqual(len(tts.split()), redaccion.TTS_MAX_PALABRAS)


if __name__ == "__main__":
    unittest.main()
