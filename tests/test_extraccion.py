"""Pruebas de regresion del extractor de mutant_stability y de la revision. Cada frase es una que
produjo una fila MAL en alguna version: atribucion a la enzima equivocada, incremento leido como
valor absoluto, polimero confundido con enzima, etc.

    uv run python -m unittest discover -s tests -v
"""

import unittest

from data_pipeline.curation import revisar_mutantes as rev
from data_pipeline.curation.extraer_mutantes import filas_de_oracion


def una(oracion):
    filas = filas_de_oracion(oracion)
    return [(f["enzyme"], f["mutations"], f["metric"], f["value"], f["_confianza"]) for f in filas]


class Extraccion(unittest.TestCase):
    def test_valor_directo_con_enzima_clara(self):
        self.assertEqual(una("While LCC displays high thermostability ( T m = 86°C), initial testing revealed"),
                         [("LCC", "", "Tm", 86.0, "alta")])

    def test_delta_tm_no_es_tm(self):
        # "Δ T m = 24.9" se leyo como Tm = 24.9
        r = una("(TfCut2, Δ T m = 24.9 °C;")
        self.assertTrue(all(m == "dTm" for _, _, m, _, _ in r) and r)

    def test_mas_alto_que_es_incremento_no_valor(self):
        # "T m was 30 C higher than that of Cut190*" se leyo como Tm = 30
        self.assertEqual([f for f in una("showing that T m was 30°C higher than that of Cut190* ( Table 1 ).") if f[2] == "Tm"], [])

    def test_triple_mutante_no_es_temperatura_de_fusion(self):
        self.assertEqual(una("the Cut190 triple mutant (TM) S176A/S226P/R228S (PDB 5ZRR) from S. viridis is the most similar"), [])

    def test_temperatura_de_fusion_de_un_polimero_no_es_de_la_enzima(self):
        self.assertEqual(una("PBSA has a T g of −43 °C and T m of 89 °C ( Table S2 )."), [])
        self.assertEqual(una("the melting temperature was 57 °C for MW 14 000 PCL and 60 °C for MW 80,000"), [])

    def test_rangos_no_son_un_tm(self):
        self.assertEqual(una("the T m of the 14 proteins ranged from 36.4 °C to 80.1 °C (Fig. 2b )"), [])

    def test_comparador_no_es_el_sujeto(self):
        # "compared to wild-type Is PETase and a T m of 82.5" -> el 82.5 no es de IsPETase
        for enz, *_ in una("The new variant was compared to wild-type Is PETase and a T m of 82.5 °C. 89 Therefore"):
            self.assertNotEqual(enz, "IsPETase")

    def test_respectively_baja_a_confianza_baja(self):
        r = una("confirmed their higher stability of FastPETase T m = 67°C and HotPETase T m = 82°C, respectively, compared")
        self.assertTrue(r and all(c == "baja" for *_, c in r))

    def test_for_asigna_a_la_enzima_que_sigue(self):
        r = una("( T m = 85.8°C for LCC and 93.3°C for LCC ICCG at pH 8, both")
        self.assertEqual(r[0][:4], ("LCC", "", "Tm", 85.8))

    def test_variantes_no_se_confunden_con_la_base(self):
        r = una("the designed variants LCC-ICCG-NM ( T m = 92.4 °C) and Kubu-P M12 -NM ( T m = 92.9 °C) retained")
        self.assertEqual(r[0][0], "LCC-ICCG-NM")
        self.assertEqual(una("Ca PETase M8 exhibited a T m value of 80.7 °C")[0][0], "CaPETase M8")

    def test_increase_of_es_incremento_e_increased_to_es_absoluto(self):
        self.assertEqual(una("DuraPETase showed a T m increase of 31 °C and over 300-fold")[0][2], "dTm")
        self.assertEqual(una("The T m value of DuraPETase was increased to 77°C, which was enhanced")[0][2:4], ("Tm", 77.0))

    def test_triple_mutante_en_tres_letras(self):
        r = una("the obtained triple-mutated Is PETase—Ser121Glu/Asp186His/Arg280Ala—had a Tm of 57.62 °C.")
        self.assertEqual(r[0][1], "S121E,D186H,R280A")

    def test_valor_dependiente_del_medio_no_es_alta(self):
        r = una("LCC-ICCG was destabilised by MeCN, with the apparent T m decreasing to 86.3 °C at 20% (v/v) MeCN")
        self.assertTrue(r and all(c != "alta" for *_, c in r))

    def test_toda_fila_lleva_la_cita_con_el_valor(self):
        for o in ("While LCC displays high thermostability ( T m = 86°C), initial testing revealed",
                  "the T m value of IsPETase W/T and C203A were 46.8 and 33.6 °C, respectively"):
            for f in filas_de_oracion(o):
                self.assertIn(f["_literal"], f["evidence_span"])


class Revision(unittest.TestCase):
    def fila(self):
        return {"evidence_span": "LCC has a Tm of 86 C", "conditions": ""}

    def resp(self, q1="s", q2="s", q3="s", q4="s", **corr):
        r = {"id": "x", "q1_valor_en_frase": q1, "q2_enzima_en_frase": q2, "q3_metrica_correcta": q3,
             "q4_condiciones": q4, "enzima_corr": "", "mutaciones_corr": "", "metrica_corr": "",
             "valor_corr": "", "condiciones_corr": ""}
        r.update(corr)
        return r

    def decidir(self, **kw):
        errores = []
        res = rev._decidir(self.resp(**kw), self.fila(), 2, errores)
        return res, errores

    def test_todo_bien_es_ok(self):
        (d, motivos, cambios), err = self.decidir()
        self.assertEqual((d, motivos, err), ("ok", [], []))

    def test_enzima_equivocada_con_correccion_corrige(self):
        (d, m, c), _ = self.decidir(q2="n", enzima_corr="TfCut2")
        self.assertEqual((d, m, c["enzyme"]), ("corregir", ["enzima_equivocada"], "TfCut2"))

    def test_sin_la_correccion_necesaria_se_descarta(self):
        (d, m, _), _ = self.decidir(q1="n")
        self.assertEqual((d, m), ("descartar", ["valor_no_respaldado"]))

    def test_condiciones_no_precisables_descarta(self):
        (d, m, _), _ = self.decidir(q4="n")
        self.assertEqual((d, m), ("descartar", ["condiciones_no_precisables"]))

    def test_condicion_anotada_es_ok_y_se_guarda(self):
        (d, _, c), _ = self.decidir(q4="a", condiciones_corr="con 10 mM CaCl2")
        self.assertEqual(d, "ok")
        self.assertIn("CaCl2", c["conditions"])

    def test_el_valor_corregido_debe_estar_en_la_frase(self):
        res, err = self.decidir(q1="n", valor_corr="999")
        self.assertIsNone(res)
        self.assertTrue(any("no aparece en la frase" in e for e in err))

    def test_fila_a_medias_es_error(self):
        errores = []
        r = self.resp(q3="", q4="")
        self.assertIsNone(rev._decidir(r, self.fila(), 2, errores))
        self.assertTrue(errores)

    def test_wilson(self):
        self.assertAlmostEqual(rev.wilson_inferior(30, 30), 0.886, places=2)
        self.assertAlmostEqual(rev.wilson_inferior(28, 30), 0.787, places=2)
        self.assertIsNone(rev.wilson_inferior(0, 0))


if __name__ == "__main__":
    unittest.main()
