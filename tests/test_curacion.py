"""Pruebas de regresion de la curacion. Cada caso fija un error REAL que se encontro al revisar los
datos: si vuelve, la prueba falla. No tocan Databricks ni la red.

    uv run python -m unittest discover -s tests -v
"""

import unittest

from data_pipeline.curation import curar, etiquetar


def doc(doc_id, source, tipo, doi="", titulo="Un titulo suficientemente largo para colapsar", abstract="x" * 80,
        full_text="", year=2024):
    return {"doc_id": doc_id, "source": source, "doc_type": tipo, "doi": doi, "title": titulo,
            "abstract": abstract, "full_text": full_text, "content_hash": doc_id, "year": year,
            "license": "cc-by", "metadata": "{}"}


class Deduplicacion(unittest.TestCase):
    def test_una_estructura_pdb_no_se_absorbe_en_el_articulo(self):
        # El DOI de una estructura es el del articulo que la describe: no es un duplicado.
        docs = [doc("europepmc:1", "europepmc", "article", "10.1/a", full_text="texto " * 50),
                doc("pdb:5XJH", "pdb", "structure", "10.1/a"), doc("pdb:5XJI", "pdb", "structure", "10.1/a")]
        elegidos, absorbidos = curar.deduplicar(docs)
        self.assertEqual({d["doc_id"] for d in elegidos}, {"europepmc:1", "pdb:5XJH", "pdb:5XJI"})
        self.assertEqual(absorbidos, {})

    def test_dos_articulos_con_el_mismo_doi_se_colapsan_y_gana_europepmc_con_texto(self):
        docs = [doc("openalex:W1", "openalex", "article", "10.1/b"),
                doc("europepmc:2", "europepmc", "article", "10.1/b", full_text="texto " * 50)]
        elegidos, absorbidos = curar.deduplicar(docs)
        self.assertEqual([d["doc_id"] for d in elegidos], ["europepmc:2"])
        self.assertEqual(absorbidos, {"europepmc:2": ["openalex:W1"]})

    def test_versiones_de_un_dataset_con_titulo_identico_se_colapsan_y_gana_zenodo(self):
        t = "HyDB an observation corpus and sequence registry for PET hydrolases"
        docs = [doc("openalex:W9", "openalex", "dataset", "10.5/x", t, abstract="y" * 200),
                doc("zenodo:1", "zenodo", "dataset", "10.5/y", t)]
        elegidos, _ = curar.deduplicar(docs)
        self.assertEqual([d["doc_id"] for d in elegidos], ["zenodo:1"])

    def test_un_articulo_con_el_titulo_de_un_dataset_no_se_colapsa(self):
        t = "HyDB an observation corpus and sequence registry for PET hydrolases"
        docs = [doc("zenodo:1", "zenodo", "dataset", "10.5/y", t), doc("openalex:W2", "openalex", "article", "10.5/z", t)]
        self.assertEqual(len(curar.deduplicar(docs)[0]), 2)

    def test_titulos_cortos_o_genericos_no_se_colapsan(self):
        docs = [doc("zenodo:1", "zenodo", "dataset", "10.5/a", "Dataset"), doc("zenodo:2", "zenodo", "dataset", "10.5/b", "Dataset")]
        self.assertEqual(len(curar.deduplicar(docs)[0]), 2)


class Limpieza(unittest.TestCase):
    def test_html_doble_escape_y_subindices(self):
        self.assertEqual(curar.limpiar_html("En &lt;i&gt;E. coli&lt;/i&gt; k<sub>cat</sub>/K<sub>m</sub> y p<0.05"),
                         "En E. coli kcat/Km y p<0.05")

    def test_los_correos_se_enmascaran(self):
        t = curar.limpiar_html("Correspondence: ana.perez@uni.edu y lab@x.org")
        self.assertNotIn("@", t)
        self.assertIn("[correo omitido]", t)

    def test_secciones_administrativas(self):
        for s in ("Data Availability Statement", "Contributor Information", "Lead contact", "Technical contact",
                  "Authors' contributions", "Reporting summary", "Publisher's note", "Associated Data"):
            self.assertTrue(curar.ADMIN.match(s), s)
        # "Correspondence between plastic content..." es una seccion CIENTIFICA que un patron
        # demasiado amplio llego a descartar.
        for s in ("Correspondence", "Correspondence to", "Correspondence and requests for materials"):
            self.assertTrue(curar.ADMIN.match(s), s)
        for s in ("Introduction", "Results and Discussion", "Contact angle measurements", "Kinetic parameters",
                  "Correspondence between plastic content and microplastic abundance"):
            self.assertFalse(curar.ADMIN.match(s), s)

    def test_una_seccion_sin_cuerpo_no_genera_un_trozo_con_su_titulo(self):
        d = doc("europepmc:3", "europepmc", "article", "10.1/c", abstract="",
                full_text="## Glossary\n\n## Results\n\n" + "La enzima degrada el plastico a 60 grados. " * 30)
        trozos = curar.trocear(d)
        self.assertEqual({m for _, _, m in trozos if m}, {"solo_titulo_de_seccion"})
        self.assertTrue(any(m is None for _, _, m in trozos))

    def test_una_tabla_se_conserva_entera_en_un_solo_trozo(self):
        tabla = "Table 2 Kinetics. Enzyme kcat Km " + " ".join(f"E{i} {i}.1 {i}.2" for i in range(400))
        d = doc("europepmc:4", "europepmc", "article", "10.1/d", abstract="", full_text=f"## Results\n\ntexto previo.\n{tabla}\nfin.")
        trozos = [t for _, t, m in curar.trocear(d) if t.startswith("Table 2")]
        self.assertEqual(len(trozos), 1)
        self.assertIn("E399", trozos[0])


class IdiomaYRelevancia(unittest.TestCase):
    def test_idiomas(self):
        self.assertEqual(curar.detectar_idioma("The enzyme degrades polyethylene terephthalate and the results of this study show that it is active in the lab"), "en")
        self.assertEqual(curar.detectar_idioma("La degradación de plásticos por enzimas es un tema que se ha estudiado para el medio ambiente y también para la industria"), "es")
        self.assertEqual(curar.detectar_idioma("聚对苯二甲酸乙二醇酯的酶降解研究进展与应用前景分析及其在环境治理中的作用"), "cjk")
        self.assertEqual(curar.detectar_idioma("corto"), "desconocido")

    def test_relevancia_sobre_titulo_y_resumen(self):
        r = curar.relevancia_doc
        self.assertEqual(r("article", "FAST-PETase", "We engineered a PET hydrolase enzyme for polyethylene terephthalate " * 3, ""), "nucleo_pet_enzima")
        self.assertEqual(r("article", "Microplastics in macrophages", "Plastic particles affect macrophage polarization in cells " * 4, ""), "plasticos_sin_enzima")
        self.assertEqual(r("article", "HRV 3C protease engineering", "A protease for fusion tag removal in biotechnology " * 5, ""), "fuera_de_alcance")
        self.assertEqual(r("structure", "x", "", ""), "estructura")
        # PET = tomografia no es el plastico
        self.assertNotEqual(r("article", "PET/CT imaging", "PET/CT enzyme uptake in tumours " * 5, ""), "nucleo_pet_enzima")


class Etiquetas(unittest.TestCase):
    def test_subtema_claro(self):
        s1, _ = etiquetar.asignar_subtemas("Molecular dynamics and QM/MM of the catalytic triad and oxyanion hole; binding energy in kcal/mol in the active site.")
        self.assertEqual(s1, "mecanismo_simulacion")

    def test_texto_sin_tema_queda_sin_subtema(self):
        self.assertEqual(etiquetar.asignar_subtemas("Samples were centrifuged at 4000 g for ten minutes and stored."), (None, None))

    def test_tipo_de_evidencia_por_seccion(self):
        e = etiquetar.tipo_evidencia
        self.assertEqual(e("Resumen", "article", False), "resumen")
        self.assertEqual(e("Materials and Methods", "article", False), "metodos")
        self.assertEqual(e("Results and Discussion", "article", False), "resultados")
        self.assertEqual(e("Discussion", "article", False), "discusion")
        self.assertEqual(e("Cualquier cosa", "article", True), "tabla")
        self.assertEqual(e("Cualquier cosa", "structure", False), "estructura")

    def test_enzimas_omite_los_nombres_genericos(self):
        self.assertEqual(etiquetar.enzimas("The PETase and the cutinase were compared with IsPETase and FAST-PETase."),
                         ["IsPETase", "FAST-PETase"])

    def test_estructuras_no_llevan_subtema(self):
        self.assertIsNone(etiquetar.etiquetar_trozo("QM/MM molecular dynamics docking", "Resumen", "structure", False)["subtopic"])


if __name__ == "__main__":
    unittest.main()
