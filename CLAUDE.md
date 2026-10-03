# Reglas del proyecto para agentes de código

## Git

- **Sin atribución a herramientas de IA.** Ningún commit, PR, tag ni mensaje lleva líneas
  `Co-Authored-By`, `Claude-Session`, "Generated with" ni menciones a Claude, Anthropic u otro
  asistente. El autor es la persona del equipo que hace el commit.
- Ramas: `main` (general, estable), `test` (pruebas e integración), `algoritmos` (curación y
  modelos). No crear otras ramas sin que el equipo lo pida.
- Nunca subir `.env`, credenciales ni datos: el corpus vive en `/Volumes/workspace/lab/raw`.

## Alcance

Recuperación de literatura publicada y análisis estadístico sobre datos ya publicados.
No diseñar ni generar secuencias, no proponer modificaciones biológicas, no escribir protocolos
de laboratorio. Ver `docs/ALCANCE.md`.

## Datos

- Esquema `workspace.lab` (perfil CLI `hack`). Guía en `docs/CURACION.md`.
- Nada entra a `documents_curated` sin aprobación humana. Toda afirmación lleva `doc_id`.
