"""El endpoint de admision de hipotesis, sin red ni Databricks: los cargadores se
sustituyen por datos en memoria. Comprueba el transporte, no el criterio (eso vive
en test_procedencia.py).
"""

import unittest

from agent_lab import procedencia_fuente
from tests.test_procedencia import COLUMNAS, CORPUS, FRASE, hip, respaldo

try:
    from fastapi.testclient import TestClient

    from ar_vr_bridge.app import app
    DISPONIBLE = True
except Exception:  # pragma: no cover - entorno sin fastapi/httpx
    DISPONIBLE = False


@unittest.skipUnless(DISPONIBLE, "requiere fastapi y httpx")
class Endpoint(unittest.TestCase):
    def setUp(self):
        self.originales = (procedencia_fuente.cargar_corpus,
                           procedencia_fuente.cargar_registros,
                           procedencia_fuente.cargar_columnas)
        procedencia_fuente.cargar_corpus = lambda ids, db=None: dict(CORPUS)
        procedencia_fuente.cargar_registros = lambda ids, db=None: {}
        procedencia_fuente.cargar_columnas = lambda db=None: set(COLUMNAS)
        self.cliente = TestClient(app)

    def tearDown(self):
        (procedencia_fuente.cargar_corpus, procedencia_fuente.cargar_registros,
         procedencia_fuente.cargar_columnas) = self.originales

    def test_una_hipotesis_respaldada_se_admite_con_su_recibo(self):
        r = self.cliente.post("/api/v1/hypothesis", json=hip([respaldo(value=85.8)]))
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertTrue(cuerpo["admitted"], cuerpo)
        self.assertEqual(len(cuerpo["receipt_hash"]), 16)
        self.assertTrue(all(c["passed"] for c in cuerpo["checks"] if c["fatal"]))

    def test_una_frase_inventada_se_rechaza_y_dice_por_que(self):
        falsa = "LCC-ICCG reached a melting temperature of 85.8 C with full crystalline activity."
        r = self.cliente.post("/api/v1/hypothesis", json=hip([respaldo(span=falsa)]))
        cuerpo = r.json()
        self.assertFalse(cuerpo["admitted"])
        self.assertIn("respaldo[0].span_literal", cuerpo["failures"])

    def test_una_pregunta_abierta_sin_respaldo_no_entra(self):
        r = self.cliente.post("/api/v1/hypothesis", json={
            "statement": "Que propiedades predicen la actividad a 60 grados centigrados",
            "prediction": "", "variables": [], "respaldo": []})
        cuerpo = r.json()
        self.assertEqual(cuerpo["verdict"], "RECHAZADA")
        self.assertIn("respaldo_presente", cuerpo["failures"])

    def test_si_el_corpus_no_se_puede_leer_no_se_admite_a_ciegas(self):
        def caido(*a, **k):
            raise RuntimeError("warehouse apagado")
        procedencia_fuente.cargar_corpus = caido
        r = self.cliente.post("/api/v1/hypothesis", json=hip([respaldo(span=FRASE)]))
        cuerpo = r.json()
        self.assertEqual(cuerpo["verdict"], "RECHAZADA")
        self.assertIn("corpus_inaccesible", cuerpo["failures"])


if __name__ == "__main__":
    unittest.main()
