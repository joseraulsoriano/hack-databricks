"""Pruebas de la puerta de procedencia. Cada caso fija una forma concreta de colar
un dato que no se sostiene. No tocan Databricks ni la red.

    uv run python -m unittest discover -s tests -v
"""

import unittest

from agent_lab import procedencia

FRASE = ("The engineered variant LCC-ICCG showed a melting temperature of 85.8 degrees C, "
         "an increase over the wild-type enzyme.")
OTRA = ("CaPETase M9 retained activity after incubation at 60 degrees C for 24 hours in "
        "buffer without added calcium ions.")

CORPUS = {
    "europepmc:111": {"text": "Introduction. " + FRASE + " Further work is needed.",
                      "approved_by": "human:revisor", "source": "europepmc"},
    "openalex:W222": {"text": "Abstract. " + OTRA, "approved_by": "human:revisor",
                      "source": "openalex"},
    "europepmc:333": {"text": "A paper with no approval yet. " + OTRA,
                      "approved_by": "", "source": "europepmc"},
}

COLUMNAS = {"temperature_c", "ph", "molecular_weight", "isoelectric_point", "activity"}


def hip(respaldo, **extra):
    base = {"statement": "La termoestabilidad de una PET hidrolasa sube con mutaciones en el sitio activo",
            "prediction": "Las variantes con Tm publicada mayor mantienen actividad medida a 60 grados",
            "variables": ["temperature_c", "activity"],
            "respaldo": respaldo, "submitted_by": "human:equipo"}
    base.update(extra)
    return base


def respaldo(doc_id="europepmc:111", span=FRASE, **extra):
    r = {"doc_id": doc_id, "evidence_span": span}
    r.update(extra)
    return r


class SpanLiteral(unittest.TestCase):
    def test_una_frase_que_esta_en_el_documento_pasa(self):
        r = procedencia.verificar(hip([respaldo(), respaldo("openalex:W222", OTRA)]),
                                  CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "ADMITIDA", procedencia.explicar(r))

    def test_una_frase_inventada_se_rechaza(self):
        # El modo de fallo que importa: el extractor resume o parafrasea y suena bien.
        falsa = "LCC-ICCG showed a melting temperature of 85.8 C and full activity on crystalline PET."
        r = procedencia.verificar(hip([respaldo(span=falsa)]), CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "RECHAZADA")
        self.assertIn("respaldo[0].span_literal", r["fallos"])

    def test_una_frase_atribuida_al_documento_equivocado_dice_donde_esta(self):
        # Caso clasico: la evidencia es real pero el doc_id es de otro articulo.
        r = procedencia.verificar(hip([respaldo(doc_id="europepmc:111", span=OTRA)]),
                                  CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "RECHAZADA")
        fallo = [c for c in r["checks"] if c["name"] == "respaldo[0].span_literal"][0]
        self.assertIn("openalex:W222", fallo["detail"])

    def test_un_fragmento_corto_no_cuenta_como_evidencia(self):
        r = procedencia.verificar(hip([respaldo(span="85.8 degrees C")]), CORPUS, columnas=COLUMNAS)
        self.assertIn("respaldo[0].span_suficiente", r["fallos"])

    def test_el_apostrofo_tipografico_no_rompe_el_cotejo(self):
        # El corpus trae "PETase’s" (U+2019); un agente que copia por JSON manda "PETase's".
        doc = {"europepmc:ap": {"text": "The basis of Kb PETase’s enhanced thermostability was studied.",
                                "approved_by": "human:revisor", "source": "europepmc"}}
        span = "The basis of Kb PETase's enhanced thermostability was studied."
        r = procedencia.verificar(hip([respaldo("europepmc:ap", span)]), {**CORPUS, **doc},
                                  columnas=COLUMNAS)
        self.assertNotIn("respaldo[0].span_literal", r["fallos"], procedencia.explicar(r))

    def test_el_guion_unicode_no_rompe_el_cotejo(self):
        # LCC-ICCG aparece con U+2010 en unos articulos y con guion ASCII en otros.
        r = procedencia.verificar(hip([respaldo(span=FRASE.replace("LCC-ICCG", "LCC‐ICCG"))]),
                                  CORPUS, columnas=COLUMNAS)
        self.assertNotIn("respaldo[0].span_literal", r["fallos"], procedencia.explicar(r))


class Numeros(unittest.TestCase):
    def test_el_valor_tiene_que_estar_en_su_propia_frase(self):
        r = procedencia.verificar(hip([respaldo(value=85.8)]), CORPUS, columnas=COLUMNAS)
        self.assertNotIn("respaldo[0].valor_en_span", r["fallos"], procedencia.explicar(r))

    def test_un_valor_que_no_esta_en_la_frase_se_rechaza(self):
        r = procedencia.verificar(hip([respaldo(value=92.4)]), CORPUS, columnas=COLUMNAS)
        self.assertIn("respaldo[0].valor_en_span", r["fallos"])

    def test_un_numero_no_se_da_por_bueno_porque_sea_prefijo_de_otro(self):
        # "85.8" esta en la frase; el valor 85 NO lo esta como afirmacion propia.
        self.assertFalse(procedencia.valor_aparece(85, FRASE))
        self.assertFalse(procedencia.valor_aparece(8, FRASE))
        self.assertTrue(procedencia.valor_aparece(85.8, FRASE))

    def test_coma_decimal(self):
        self.assertTrue(procedencia.valor_aparece(85.8, "Tm de 85,8 grados"))

    def test_un_entero_escrito_sin_decimales(self):
        self.assertTrue(procedencia.valor_aparece(60.0, OTRA))


class Aprobacion(unittest.TestCase):
    def test_un_documento_sin_aprobacion_humana_no_sostiene_una_hipotesis(self):
        r = procedencia.verificar(hip([respaldo("europepmc:333", OTRA)]), CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "RECHAZADA")
        self.assertIn("respaldo[0].doc_aprobado", r["fallos"])

    def test_un_doc_id_que_no_existe_se_rechaza(self):
        r = procedencia.verificar(hip([respaldo("europepmc:999")]), CORPUS, columnas=COLUMNAS)
        self.assertIn("respaldo[0].doc_existe", r["fallos"])


class Registros(unittest.TestCase):
    REGISTROS = {
        "rec_ok": {"value": 85.8, "unit": "C", "verified": True, "doc_id": "europepmc:111",
                   "extracted_by": "human:revisor"},
        "rec_sin_revisar": {"value": 85.8, "unit": "C", "verified": False,
                            "doc_id": "europepmc:111", "extracted_by": "agent:regex_extractor"},
    }

    def test_una_fila_verificada_pasa(self):
        r = procedencia.verificar(
            hip([respaldo(value=85.8, record_id="rec_ok"), respaldo("openalex:W222", OTRA)]),
            CORPUS, self.REGISTROS, COLUMNAS)
        self.assertEqual(r["veredicto"], "ADMITIDA", procedencia.explicar(r))

    def test_una_fila_que_nadie_reviso_no_entra(self):
        # Esto es lo que separa el dato trazable de la salida cruda del extractor.
        r = procedencia.verificar(hip([respaldo(value=85.8, record_id="rec_sin_revisar")]),
                                  CORPUS, self.REGISTROS, COLUMNAS)
        self.assertEqual(r["veredicto"], "RECHAZADA")
        self.assertIn("respaldo[0].registro_verificado", r["fallos"])

    def test_el_valor_afirmado_tiene_que_ser_el_de_la_fila(self):
        r = procedencia.verificar(hip([respaldo(span=FRASE, value=85.8, record_id="rec_ok")]),
                                  CORPUS, {"rec_ok": {**self.REGISTROS["rec_ok"], "value": 70.0}},
                                  COLUMNAS)
        self.assertIn("respaldo[0].valor_coincide_registro", r["fallos"])


class Estructura(unittest.TestCase):
    def test_una_pregunta_sin_evidencia_no_es_una_hipotesis(self):
        r = procedencia.verificar(hip([]), CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "RECHAZADA")
        self.assertIn("respaldo_presente", r["fallos"])

    def test_sin_prediccion_medible_no_entra(self):
        r = procedencia.verificar(hip([respaldo()], prediction=""), CORPUS, columnas=COLUMNAS)
        self.assertIn("prediccion_presente", r["fallos"])

    def test_una_variable_que_no_es_columna_se_rechaza(self):
        r = procedencia.verificar(hip([respaldo()], variables=["tm_predicho"]), CORPUS, columnas=COLUMNAS)
        self.assertIn("variables_existen", r["fallos"])

    def test_una_sola_fuente_avisa_pero_no_rechaza(self):
        r = procedencia.verificar(hip([respaldo()]), CORPUS, columnas=COLUMNAS)
        self.assertEqual(r["veredicto"], "ADMITIDA_CON_AVISOS")
        self.assertTrue(any("fuentes_distintas" in a for a in r["avisos"]))


class Reproducibilidad(unittest.TestCase):
    def test_el_mismo_input_da_el_mismo_recibo(self):
        a = procedencia.verificar(hip([respaldo()]), CORPUS, columnas=COLUMNAS)
        b = procedencia.verificar(hip([respaldo()]), CORPUS, columnas=COLUMNAS)
        self.assertEqual(a["recibo_hash"], b["recibo_hash"])

    def test_cambiar_un_decimal_cambia_el_recibo(self):
        a = procedencia.verificar(hip([respaldo(value=85.8)]), CORPUS, columnas=COLUMNAS)
        b = procedencia.verificar(hip([respaldo(value=85.9)]), CORPUS, columnas=COLUMNAS)
        self.assertNotEqual(a["recibo_hash"], b["recibo_hash"])


if __name__ == "__main__":
    unittest.main()
