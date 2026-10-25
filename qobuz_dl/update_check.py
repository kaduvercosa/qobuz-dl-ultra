# ==============================================================================
# MÓDULO: update_check.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Descobre se existe uma versão mais nova no PyPI do que a instalada.
#
# DECISÕES:
#   - Fonte = PyPI (é de onde `pip install -U` instala), não o GitHub: um release
#     no GitHub pode existir antes de o pacote estar publicado.
#   - SEM CACHE, de propósito: nada é gravado em disco e a requisição pede
#     "Cache-Control: no-cache" (mais um parâmetro que muda a cada minuto, para
#     furar o cache da CDN). Assim o aviso aparece poucos minutos depois do
#     lançamento e some assim que a pessoa atualiza.
#   - Desligar a checagem: variável de ambiente QOBUZ_DL_NO_UPDATE_CHECK=1.
#   - Erros de rede/JSON SOBEM como exceção: quem chama decide o que fazer
#     (a tela inicial e o aviso em segundo plano só registram em debug).
# ==============================================================================

import os
import time
from typing import Optional

from packaging.version import Version

from qobuz_dl.http_download import make_sync_client

PACOTE = "qobuz-dl-ultra"
PYPI_URL = f"https://pypi.org/pypi/{PACOTE}/json"
COMANDO_ATUALIZAR = f"pip install -U {PACOTE}"
ENV_DESLIGAR = "QOBUZ_DL_NO_UPDATE_CHECK"

_SEM_CACHE = {"Cache-Control": "no-cache", "Pragma": "no-cache"}


def desativada() -> bool:
    """True se a pessoa desligou a checagem (QOBUZ_DL_NO_UPDATE_CHECK)."""
    return os.environ.get(ENV_DESLIGAR, "").strip().lower() not in ("", "0", "false")


def ultima_versao_pypi(timeout: float = 2.0) -> str:
    """Última versão publicada no PyPI, consultada ao vivo (sem cache)."""
    url = f"{PYPI_URL}?t={int(time.time() // 60)}"
    with make_sync_client(timeout=timeout, headers=_SEM_CACHE) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    return str(payload["info"]["version"]).lstrip("vV")


def nova_versao_disponivel(
    atual: Optional[str] = None, timeout: float = 2.0
) -> Optional[str]:
    """A versão do PyPI (texto) se for MAIS NOVA que a instalada; senão None."""
    if atual is None:
        from qobuz_dl import __version__ as atual
    remota = ultima_versao_pypi(timeout=timeout)
    return remota if Version(remota) > Version(atual) else None
