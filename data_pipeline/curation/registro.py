"""Registro de decisiones de curacion en workspace.lab.research_record.

Las reglas del repo piden que cada decision relevante quede reconstruible. Esta
funcion deja un evento 'approval' con quien aprobo, que y cuantas filas. Solo se
llama al publicar (--aprobar), nunca en una corrida de propuesta.
"""

import json
import uuid


def registrar_aprobacion(db, aprobador: str, resumen: str, refs: list[str],
                         detalle: dict | None = None) -> None:
    db.sql("""
        INSERT INTO workspace.lab.research_record
          (event_id, session_id, ts, from_agent, to_agent, kind, flow, weight,
           summary, refs, payload)
        VALUES (:evento, :sesion, current_timestamp(), :quien, 'curation', 'approval',
                'evidence', 1.0, :resumen, from_json(:refs, 'ARRAY<STRING>'), :payload)""",
           params={"evento": str(uuid.uuid4()), "sesion": "curation",
                   "quien": f"human:{aprobador}", "resumen": resumen,
                   "refs": json.dumps(sorted(set(refs))[:200]),
                   "payload": json.dumps(detalle or {}, ensure_ascii=False)})
    print(f"   registrado en research_record (aprobado por {aprobador})")
