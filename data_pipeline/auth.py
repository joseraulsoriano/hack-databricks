"""Un solo sitio donde se decide cómo autenticar contra Databricks.

Hay dos entornos y no se autentican igual:

- **Tu portátil**: credenciales en `~/.databrickscfg`, perfil `hack`.
- **Dentro de una Databricks App**: no hay `.databrickscfg`. La plataforma inyecta
  las credenciales del *service principal* de la app en el entorno
  (`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` / `DATABRICKS_HOST`), y el
  SDK las toma solo si **no** se le pasa un `profile`. Pasarle uno que no existe
  revienta con `config file not found` — que fue el primer intento.

Por eso la regla es: si el entorno trae credenciales inyectadas, no se nombra
perfil; si no, se usa el de siempre.
"""

import os

from databricks.sdk import WorkspaceClient

PERFIL_POR_DEFECTO = "hack"


def en_databricks_app() -> bool:
    """True cuando el proceso corre dentro de una Databricks App."""
    return bool(os.environ.get("DATABRICKS_CLIENT_ID")
                or os.environ.get("DATABRICKS_APP_NAME"))


def perfil(explicito: str | None = None) -> str | None:
    """Perfil a usar, o None para que el SDK resuelva por el entorno."""
    if explicito:
        return explicito
    if en_databricks_app():
        return None
    return os.environ.get("DATABRICKS_CONFIG_PROFILE", PERFIL_POR_DEFECTO)


def workspace(explicito: str | None = None) -> WorkspaceClient:
    """Cliente del workspace válido en los dos entornos."""
    p = perfil(explicito)
    return WorkspaceClient(profile=p) if p else WorkspaceClient()
