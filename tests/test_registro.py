"""La traza del puente en research_record. Sin red: `registrar` se sustituye.

Lo que se fija aqui: que anotar NO bloquee, que NO rompa cuando el warehouse falla
y que se pueda apagar. Una demo en vivo no puede caerse porque el registro falle.
"""

import asyncio
import os
import unittest

from ar_vr_bridge import registro


class Anotar(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orig = registro.registrar
        self.escrito = []
        os.environ["RESEARCH_RECORD"] = "1"

    def tearDown(self):
        registro.registrar = self.orig
        os.environ.pop("RESEARCH_RECORD", None)

    async def _vaciar(self):
        """Espera a las tareas en vuelo, que en produccion nadie espera."""
        while registro._en_vuelo:
            await asyncio.gather(*list(registro._en_vuelo), return_exceptions=True)

    async def test_la_traza_se_escribe_en_segundo_plano(self):
        registro.registrar = lambda **kw: self.escrito.append(kw)
        registro.anotar("handoff", "consulta recibida", session_id="q_1",
                        from_agent="viewer", refs=["europepmc:1"])
        self.assertEqual(self.escrito, [], "anotar no debe escribir de forma sincrona")
        await self._vaciar()
        self.assertEqual(len(self.escrito), 1)
        self.assertEqual(self.escrito[0]["kind"], "handoff")
        self.assertEqual(self.escrito[0]["session_id"], "q_1")

    async def test_si_el_warehouse_falla_no_se_propaga(self):
        def caido(**kw):
            raise RuntimeError("warehouse apagado")
        registro.registrar = caido
        registro.anotar("handoff", "consulta", session_id="q_2", from_agent="viewer")
        await self._vaciar()   # si _intentar no tragara la excepcion, esto fallaria

    async def test_se_puede_apagar(self):
        os.environ["RESEARCH_RECORD"] = "0"
        registro.registrar = lambda **kw: self.escrito.append(kw)
        registro.anotar("handoff", "consulta", session_id="q_3", from_agent="viewer")
        await self._vaciar()
        self.assertEqual(self.escrito, [])

    async def test_las_tareas_se_retienen_para_que_no_las_recoja_el_gc(self):
        registro.registrar = lambda **kw: self.escrito.append(kw)
        registro.anotar("handoff", "consulta", session_id="q_4", from_agent="viewer")
        self.assertEqual(len(registro._en_vuelo), 1)
        await self._vaciar()
        self.assertEqual(len(registro._en_vuelo), 0, "y se sueltan al terminar")


class SinBucleDeEventos(unittest.TestCase):
    def test_anotar_desde_codigo_sincrono_no_revienta(self):
        # Un script o una prueba sincrona llama a anotar: no hay loop, no se anota.
        registro.anotar("handoff", "sin loop", session_id="q_5", from_agent="cli")


if __name__ == "__main__":
    unittest.main()
