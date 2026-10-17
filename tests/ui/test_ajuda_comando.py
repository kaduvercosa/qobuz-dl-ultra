import io
import re

import pytest

from qobuz_dl import home_screen as hs
from qobuz_dl import ui
from qobuz_dl.commands import qobuz_dl_args

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LARGURAS = [24, 30, 36, 40, 46, 60, 80, 95, 96, 100, 110, 140]


def _subparsers():
    p = qobuz_dl_args()
    act = next(
        a for a in p._actions
        if a.__class__.__name__ == "_SubParsersAction"
    )
    return dict(act.choices)


def _ajuda(parser, largura, monkeypatch):
    # `ui.raw_width()` prefere o tamanho do terminal real (TTY) e só depois lê
    # COLUMNS; por isso fixamos a largura direto, para o teste valer em
    # qualquer ambiente (CI, terminal de PC, a-Shell no iPad/iPhone).
    monkeypatch.setattr(ui, "raw_width", lambda: largura)
    monkeypatch.setenv("COLUMNS", str(largura))
    saida = io.StringIO()
    parser.print_help(file=saida)
    return saida.getvalue()


@pytest.mark.parametrize("largura", LARGURAS)
def test_nenhuma_linha_passa_da_tela(largura, monkeypatch):
    for nome, sp in _subparsers().items():
        for linha in _ajuda(sp, largura, monkeypatch).splitlines():
            assert hs._largura_visivel(linha) <= max(largura, hs.LARGURA_MIN), nome


@pytest.mark.parametrize("largura", [30, 60, 80, 100, 140])
def test_paineis_alinhados(largura, monkeypatch):
    caixa = min(max(largura, hs.LARGURA_MIN), hs.LARGURA_MAX)
    for nome, sp in _subparsers().items():
        for linha in _ajuda(sp, largura, monkeypatch).splitlines():
            s = linha.strip()
            if s.startswith(("│", "╭", "╰", "├")):
                assert hs._largura_visivel(s) == caixa, nome


def test_toda_opcao_aparece_na_ajuda(monkeypatch):
    for nome, sp in _subparsers().items():
        texto = ANSI.sub("", _ajuda(sp, 120, monkeypatch))
        for acao in sp._actions:
            for flag in acao.option_strings:
                assert flag in texto, (nome, flag)


def test_ajuda_traduzida_e_com_paineis(monkeypatch):
    texto = ANSI.sub("", _ajuda(_subparsers()["dl"], 80, monkeypatch))
    assert "╭─ Uso" in texto and "Opções" in texto
    assert "show this help message" not in texto


def test_ajuda_de_texto_com_quebras_nao_estoura(monkeypatch):
    # --folder-format tem um help com várias linhas e chaves {}
    texto = _ajuda(_subparsers()["dl"], 34, monkeypatch)
    assert "{album_artist}" in ANSI.sub("", texto)


PROIBIDOS = {
    "SOURCE", "DIRECTORY", "PATTERN", "DATE", "PATH", "PREFIX", "FORMAT",
    "QUERY", "FILTER_LABEL", "BLACKLIST", "DELAY", "TYPE", "NAME", "TARGET",
    "int", "[DIR]",
}  # fmt: skip


def test_rotulos_das_opcoes_em_portugues(monkeypatch):
    import argparse

    for nome, sp in _subparsers().items():
        f = sp._get_formatter()
        for a in sp._actions:
            if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):
                continue
            if a.nargs == 0:
                continue
            padrao = (
                f._get_default_metavar_for_optional(a)
                if a.option_strings
                else f._get_default_metavar_for_positional(a)
            )
            rotulo = f._format_args(a, padrao)
            palavras = set(rotulo.replace("[", " ").replace("]", " ").split())
            assert not (palavras & PROIBIDOS), (nome, rotulo)


@pytest.mark.parametrize("comando", ["dl", "interactive"])
def test_chaves_de_formato_em_painel_proprio(comando, monkeypatch):
    from qobuz_dl.commands import CHAVES_FAIXA, CHAVES_PASTA

    texto = ANSI.sub("", _ajuda(_subparsers()[comando], 100, monkeypatch))
    assert "Chaves de formato" in texto
    # a lista aparece só no painel, não repetida na descrição de -ff/-tf
    assert texto.count("album_genre") == 1
    for chave in CHAVES_PASTA.split(", ") + CHAVES_FAIXA.split(", "):
        assert chave in texto, chave


def test_chaves_de_formato_no_formato_antigo(monkeypatch):
    texto = _subparsers()["dl"]._ajuda_argparse()
    assert "Chaves de formato" in texto and "album_genre" in texto
