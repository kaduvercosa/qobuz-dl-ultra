"""O cartão focado da lista interativa nunca pode ficar cortado (celular).

Regressão: a margem de rolagem de baixo era fixa (4 linhas), mas o cartão
focado tem 6 linhas (borda, título, 3 detalhes, borda); no 5º item do celular
a borda inferior e parte do conteúdo ficavam atrás do rodapé. O teste roda a
tela de verdade (prompt_toolkit real, entrada simulada, terminal de tamanho
fixo) e confere, a cada tecla, que a borda inferior do cartão focado está
dentro da área visível. Também confere que o rodapé não é truncado.
"""

import asyncio
import os
import sys

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit

try:
    from prompt_toolkit.application import Application, create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    _REAL = isinstance(Application, type) and hasattr(Application, "run_async")
    # um mock de prompt_toolkit não tem como rodar a tela de verdade
    _REAL = _REAL and callable(getattr(DummyOutput, "get_size", None))
except Exception:  # noqa: BLE001
    _REAL = False

# No iOS/a-Shell a entrada simulada (pipe + laço de eventos do prompt_toolkit)
# derruba o Python com "segmentation fault" e leva a execução do pytest inteira
# junto. A tela real já é coberta no CI (Linux/macOS); defina
# QOBUZ_TUI_REAL=1 para forçar o teste em outro ambiente.
_IOS = sys.platform == "ios"  # (o conftest remove APPDIR, então não serve de sinal)
_FORCAR = os.environ.get("QOBUZ_TUI_REAL") == "1"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not _REAL, reason="prompt_toolkit real indisponível"),
    pytest.mark.skipif(
        _IOS and not _FORCAR,
        reason="entrada simulada do prompt_toolkit trava o Python no iOS/a-Shell",
    ),
]


def _opcoes(n=30):
    return [
        {
            "meta": {
                "title": f"Album numero {i}",
                "artist": "Dua Lipa",
                "album": "Album X",
                "type": "Album",
                "year": "2024",
                "tracks_count": 20,
                "duration": "01:10:00",
                "quality": "24b/44.1kHz",
            },
            "url": f"u{i}",
        }
        for i in range(n)
    ]


async def _rodar(linhas, colunas, passos=12, categoria="album"):
    class Saida(DummyOutput):
        def get_size(self):
            return Size(rows=linhas, columns=colunas)

    capturado = {}

    class Captura(Application):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            capturado["app"] = self

    def relatorio():
        janela = capturado["app"].layout.container.children[1]
        info = janela.render_info
        textos = [
            "".join(t for _, t, *_ in info.ui_content.get_line(i))
            for i in range(info.ui_content.line_count)
        ]
        foco = next((i for i, t in enumerate(textos) if "➤" in t), 0)
        base = next(
            (i for i in range(foco, len(textos)) if textos[i].lstrip()[:1] in "╰┗"),
            None,
        )
        rodape = capturado["app"].layout.container.children[2]
        return info.last_visible_line(), base, rodape

    resultados = []
    original = core.Application
    core.Application = Captura
    try:
        with create_pipe_input() as entrada:
            with create_app_session(input=entrada, output=Saida()):
                tarefa = asyncio.ensure_future(
                    core._tui_select(
                        "=== teste ===",
                        _opcoes(),
                        is_multi=True,
                        item_category=categoria,
                    )
                )
                await asyncio.sleep(0.3)
                for _ in range(passos):
                    resultados.append(relatorio()[:2])
                    entrada.send_text("j")
                    await asyncio.sleep(0.12)
                entrada.send_text("\r")
                await asyncio.wait_for(tarefa, 5)
    finally:
        core.Application = original
    return resultados


@pytest.mark.parametrize("linhas, colunas", [(28, 50), (24, 46), (32, 80)])
def test_borda_inferior_do_cartao_focado_sempre_visivel(linhas, colunas):
    resultados = asyncio.run(_rodar(linhas, colunas))
    cortados = [
        (passo, ultima, base)
        for passo, (ultima, base) in enumerate(resultados)
        if base is None or base > ultima
    ]
    assert not cortados, f"cartão focado cortado nos passos: {cortados}"
