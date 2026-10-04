"""Construye `answer` y `tts_text` a partir de las citas recuperadas.

**Extractivo a propósito: no se genera prosa nueva.** El contrato pide que toda
afirmación de `answer` sea rastreable a un `doc_id` de `citations`; la forma más
barata de garantizarlo es no escribir ninguna afirmación propia. Lo que se devuelve
es el pasaje citado más su atribución.

Si más adelante se decide generar prosa con un LLM del workspace, el reemplazo es
esta función y nada más — pero entonces hay que volver a demostrar que cada frase
queda respaldada, que es justo lo que hoy sale gratis.
"""

import re

TTS_MAX_PALABRAS = 40

_FIN_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


def _atribucion(cita) -> str:
    quien = cita.authors_short or (cita.title[:60] if cita.title else cita.doc_id)
    return f"{quien}{f', {cita.year}' if cita.year else ''}"


def _recortar(texto: str, maximo: int = TTS_MAX_PALABRAS) -> str:
    palabras = texto.split()
    if len(palabras) <= maximo:
        return texto
    return " ".join(palabras[:maximo]).rstrip(",;:") + "…"


def redactar(citations) -> tuple[str, str]:
    """Devuelve (answer, tts_text). `tts_text` nunca pasa de 40 palabras."""
    if not citations:
        sin = "No encuentro evidencia suficiente sobre eso en el corpus curado."
        return sin, sin

    principal = citations[0]
    pasaje = (principal.snippet or "").strip()

    if not pasaje:
        # Sin pasaje no inventamos uno: se nombra la fuente y se deja el detalle al panel.
        answer = (f"La fuente más próxima en el corpus es «{principal.title}» "
                  f"({_atribucion(principal)}, {principal.doc_id}).")
        if len(citations) > 1:
            answer += f" Hay {len(citations)} fuentes citadas en total."
        return answer, _recortar(answer)

    answer = f"{pasaje} — {_atribucion(principal)} ({principal.doc_id})."
    if len(citations) > 1:
        answer += f" Respaldan la consulta {len(citations)} fuentes citadas."

    # Para la voz: la primera frase del pasaje, recortada, con la atribución.
    primera = _FIN_DE_FRASE.split(pasaje)[0].strip()
    hablado = _recortar(primera, TTS_MAX_PALABRAS - 6)
    return answer, f"{hablado} Según {_atribucion(principal)}."
