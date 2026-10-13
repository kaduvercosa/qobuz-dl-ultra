import re
import types

import pytest

from qobuz_dl import home_screen as hs
from qobuz_dl.commands import qobuz_dl_args

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LARGURAS = [24, 30, 36, 40, 46, 60, 80, 95, 96, 100, 110, 140]


def _cmds():
    p = qobuz_dl_args()
    act = next(a for a in p._actions if a.__class__.__name__ == "_SubParsersAction")
    out = []
    for ca in act._choices_actions:
        sp = act.choices[ca.dest]
        al = [n for n, s in act.choices.items() if s is sp and n != ca.dest]
        out.append((ca.dest, ", ".join(al), ca.help))
    return out


def _flags():
    p = qobuz_dl_args()
    return [
        (", ".join(a.option_strings), a.help or "")
        for a in p._actions
        if a.option_strings and "-h" not in a.option_strings
    ]


def _render(largura, cor=False):
    return hs.render_home(_cmds(), _flags(), largura=largura, cor=cor)


@pytest.mark.parametrize("largura", LARGURAS)
def test_nenhuma_linha_passa_da_tela(largura):
    for linha in _render(largura).splitlines():
        assert hs._largura_visivel(linha) <= largura


@pytest.mark.parametrize("largura", LARGURAS)
def test_paineis_alinhados(largura):
    caixa = min(largura, hs.LARGURA_MAX)
    for linha in _render(largura).splitlines():
        s = linha.strip()
        if s.startswith(("│", "╭", "╰", "├")):
            assert hs._largura_visivel(s) == caixa


@pytest.mark.parametrize("largura", [30, 46, 80, 100])
def test_linha_horizontal_entre_comandos(largura):
    texto = _render(largura)
    painel = texto[texto.index("Comandos") :]
    painel = painel[: painel.index("╰")]
    separadores = [l for l in painel.splitlines() if l.strip().startswith("├")]
    assert len(separadores) >= len(_cmds()) - 1


def test_paineis_separados_por_linha_em_branco():
    linhas = _render(80).splitlines()
    for i, l in enumerate(linhas):
        if l.startswith("╭") and i > 0 and "Início" not in l:
            assert linhas[i - 1] == ""


def test_layout_muda_com_a_largura():
    assert "COMO USAR" in _render(100)
    assert "COMO USAR" not in _render(60)
    assert "uso " in _render(60)


def test_centraliza_acima_do_maximo():
    linhas = [l for l in _render(140).splitlines() if l.strip().startswith("╭")]
    assert all(l.startswith(" " * 15) for l in linhas)


def test_todo_comando_do_parser_tem_exemplo():
    assert {n for n, *_ in _cmds()} <= set(hs.EXEMPLOS)


def test_exemplos_sao_validos_no_parser():
    import shlex

    p = qobuz_dl_args()
    for nome, (_uso, ex) in hs.EXEMPLOS.items():
        ns, _ = p.parse_known_args(shlex.split(ex.replace("~", "/tmp")))
        assert ns.command is not None, nome


def test_comando_novo_sem_exemplo_usa_fallback():
    texto = hs.render_home(
        [("novo", "", "faz algo", "novo [x]", None)], largura=80, cor=False
    )
    assert "novo [x]" in texto


def test_cor_ligada_e_desligada():
    assert not ANSI.search(_render(80, cor=False))
    assert ANSI.search(_render(80, cor=True))


def test_aceita_dict_e_tupla():
    d = {"nome": "dl", "aliases": "", "desc": "baixa"}
    texto = hs.render_home([d, ("auth", "login", "conta")], largura=80, cor=False)
    assert "dl" in texto


def test_largura_padrao_detecta_terminal(monkeypatch):
    # Troca só o `shutil` do módulo (e não o global), para não atrapalhar o
    # próprio pytest, que também chama shutil.get_terminal_size(fallback=...).
    falso = types.SimpleNamespace(
        get_terminal_size=lambda *a, **k: types.SimpleNamespace(columns=50)
    )
    monkeypatch.setattr(hs, "shutil", falso)
    for linha in hs.render_home(_cmds(), cor=False).splitlines():
        assert hs._largura_visivel(linha) <= 50


def test_quebrar_e_cortar():
    assert all(len(l) <= 10 for l in hs._quebrar("x" * 50, 10))
    assert hs._cortar("abcdef", 4).endswith("…")
    assert hs._cortar("abc", 4) == "abc"
