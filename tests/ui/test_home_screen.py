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


class TestAvisoUpdate:
    """A linha 'versao nova disponivel' da tela inicial: uma linha so', centralizada."""

    @pytest.mark.parametrize("largura", LARGURAS)
    def test_uma_linha_que_cabe_em_qualquer_largura(self, largura):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=largura, cor=False)
        assert "\n" not in texto
        assert hs._largura_visivel(texto) <= max(largura, 20)
        assert "2.7.0" in texto

    def test_versao_completa_quando_ha_espaco(self):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=100, cor=False)
        assert "Atualização disponível" in texto
        assert "v2.6.6" in texto and "v2.7.0" in texto
        assert "pip install -U qobuz-dl-ultra" in texto

    def test_versao_curta_em_terminal_estreito(self):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=24, cor=False).strip()
        assert texto == "▲ v2.7.0 disponível"

    def test_sem_cor_nao_emite_ansi(self):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=100, cor=False)
        assert not ANSI.search(texto)

    def test_com_cor_emite_ansi(self):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=100, cor=True)
        assert ANSI.search(texto)

    def test_centralizado(self):
        texto = hs.render_aviso_update("2.6.6", "2.7.0", largura=120, cor=False)
        esquerda = len(texto) - len(texto.lstrip())
        direita = 120 - esquerda - hs._largura_visivel(texto.strip())
        assert abs(esquerda - direita) <= 1


class TestAvisoNaTelaInicial:
    def _tela(self, monkeypatch, capsys, nova):
        from qobuz_dl import cli, update_check

        monkeypatch.delenv("QOBUZ_DL_NO_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(
            update_check, "nova_versao_disponivel", lambda *a, **k: nova
        )
        cli._print_welcome_screen()
        return ANSI.sub("", capsys.readouterr().out)

    def test_aparece_quando_ha_versao_nova(self, monkeypatch, capsys):
        saida = self._tela(monkeypatch, capsys, "99.0.0")
        assert "v99.0.0" in saida
        assert "pip install -U qobuz-dl-ultra" in saida or "disponível" in saida

    def test_nao_aparece_quando_esta_atualizado(self, monkeypatch, capsys):
        saida = self._tela(monkeypatch, capsys, None)
        assert "disponível" not in saida

    def test_falha_de_rede_nao_quebra_a_tela(self, monkeypatch, capsys):
        from qobuz_dl import cli, update_check

        monkeypatch.delenv("QOBUZ_DL_NO_UPDATE_CHECK", raising=False)

        def _boom(*a, **k):
            raise OSError("sem rede")

        monkeypatch.setattr(update_check, "nova_versao_disponivel", _boom)
        cli._print_welcome_screen()
        assert "Uso:" in ANSI.sub("", capsys.readouterr().out)
