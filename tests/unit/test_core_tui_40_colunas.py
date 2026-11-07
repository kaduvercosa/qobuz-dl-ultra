"""A lista interativa de resultados cabe numa tela de 40 colunas (a-Shell no iPhone).

Roda a tela de verdade (prompt_toolkit real, entrada simulada, terminal de 40
colunas) para cada categoria e confere que nenhuma linha, de nenhuma janela
(título, lista, rodapé), passa de 40 células. Títulos longos de propósito: é
isso que costuma estourar.

Mesmas condições de `test_core_tui_cartao_visivel.py`: precisa do prompt_toolkit
real e não roda no iOS (a entrada simulada derruba o Python lá); `QOBUZ_TUI_REAL=1`
força.
"""

import asyncio
import os
import sys

import pytest

from qobuz_dl import core

try:
    from prompt_toolkit.application import Application, create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    _REAL = isinstance(Application, type) and hasattr(Application, "run_async")
    _REAL = _REAL and callable(getattr(DummyOutput, "get_size", None))
except Exception:  # noqa: BLE001
    _REAL = False

_IOS = sys.platform == "ios"
_FORCAR = os.environ.get("QOBUZ_TUI_REAL") == "1"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not _REAL, reason="prompt_toolkit real indisponível"),
    pytest.mark.skipif(
        _IOS and not _FORCAR,
        reason="entrada simulada do prompt_toolkit trava o Python no iOS/a-Shell",
    ),
]

COLUNAS = 40
LINHAS = 24

_LONGO = "Um nome bastante longo para testar o corte"
_OPCOES = {
    "album": {
        "title": _LONGO,
        "artist": "Artista Com Nome Longo",
        "type": "Album",
        "year": "2024",
        "tracks_count": 12,
        "quality": "24b/96kHz",
        "duration": "01:10:00",
    },
    "track": {
        "title": _LONGO,
        "artist": "Artista Com Nome Longo",
        "album": "Álbum com título comprido",
        "type": "Track",
        "year": "2024",
        "duration": "03:00",
        "quality": "16b/44.1kHz",
    },
    "playlist": {
        "name": _LONGO,
        "owner": "Dono Com Nome Longo",
        "count": 20,
        "duration": "04:00",
    },
    "artist": {"name": _LONGO, "count": 20},
}


def _linhas_visiveis(app):
    """Todas as linhas já desenhadas de cada janela da tela, como texto."""
    saida = []
    for janela in app.layout.container.children:
        info = getattr(janela, "render_info", None)
        if info is None:
            continue
        conteudo = info.ui_content
        for i in range(conteudo.line_count):
            saida.append("".join(t for _, t, *_ in conteudo.get_line(i)))
    return saida


async def _rodar(categoria):
    class Saida(DummyOutput):
        def get_size(self):
            return Size(rows=LINHAS, columns=COLUNAS)

    capturado = {}

    class Captura(Application):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            capturado["app"] = self

    opcoes = [{"meta": dict(_OPCOES[categoria]), "url": f"u{i}"} for i in range(6)]
    original = core.Application
    core.Application = Captura
    linhas = []
    try:
        with create_pipe_input() as entrada:
            with create_app_session(input=entrada, output=Saida()):
                tarefa = asyncio.ensure_future(
                    core._tui_select(
                        "=== teste ===",
                        opcoes,
                        is_multi=True,
                        item_category=categoria,
                    )
                )
                await asyncio.sleep(0.3)
                for _ in range(3):
                    linhas.extend(_linhas_visiveis(capturado["app"]))
                    entrada.send_text("j")
                    await asyncio.sleep(0.12)
                entrada.send_text("\r")
                await asyncio.wait_for(tarefa, 5)
    finally:
        core.Application = original
    return linhas


@pytest.mark.parametrize("categoria", sorted(_OPCOES))
def test_nenhuma_linha_passa_de_40_colunas(categoria):
    linhas = asyncio.run(_rodar(categoria))
    assert linhas, "a tela não desenhou nada"
    largas = sorted(
        {(core.get_cwidth(t), t) for t in linhas if core.get_cwidth(t) > COLUNAS},
        reverse=True,
    )
    assert not largas, f"linhas acima de {COLUNAS} colunas: {largas[:5]}"
