"""Telas e relatórios em terminal estreito (40 colunas, a-Shell no iPhone).

Cobre o quebrador de linhas com cor (`ui.wrap_ansi_line`), as linhas de resumo
(`ui.resumo_linhas`) e o relatório completo do comando `tags` com `COLUMNS=40`:
nenhuma linha pode passar da tela. A lista interativa (prompt_toolkit) tem o
teste próprio `test_core_tui_40_colunas.py`.
"""

import asyncio
import os
import re
import sys

import pytest

from qobuz_dl import retro_tags, ui

pytestmark = pytest.mark.unit

ANSI = re.compile(r"\x1b\[[0-9;]*m")
VERDE, OFF = "\x1b[32m", "\x1b[0m"


def _sem_cor(texto):
    return ANSI.sub("", texto)


def _largas(linhas, limite):
    """Linhas acima de ``limite`` células, menos as de uma palavra só (um caminho
    longo não é partido de propósito: o terminal o quebra e ele segue copiável)."""
    achadas = []
    for linha in linhas:
        visivel = _sem_cor(linha)
        if ui._cell_width(visivel) > limite and len(visivel.split()) > 1:
            achadas.append((ui._cell_width(visivel), visivel))
    return achadas


@pytest.fixture
def colunas(monkeypatch):
    """Fixa a largura do terminal (ignora o tty real de quem roda o teste)."""

    def _fixar(n):
        # `raw_width` olha o tty real antes do COLUMNS (no a-Shell o tty é real),
        # então a largura é fixada na própria fonte única.
        monkeypatch.setattr(ui, "raw_width", lambda *args, **kwargs: n)

    return _fixar


class TestWrapAnsiLine:
    def test_linha_que_cabe_volta_intacta(self):
        linha = f"{VERDE} • ok{OFF}"
        assert ui.wrap_ansi_line(linha, 40) == [linha]

    def test_quebra_entre_palavras_e_respeita_o_limite(self):
        linha = " • " + " ".join(["palavra"] * 12)
        partes = ui.wrap_ansi_line(linha, 30)
        assert len(partes) > 1
        assert all(ui._cell_width(p) <= 29 for p in partes)
        assert " ".join(p.strip() for p in partes).replace("• ", "") == " ".join(
            ["palavra"] * 12
        ).replace("  ", " ")

    def test_continuacao_alinha_sob_o_texto_do_marcador(self):
        partes = ui.wrap_ansi_line(" • " + "abc " * 15, 24)
        assert partes[0].startswith(" • ")
        assert all(p.startswith("   ") and not p.startswith("    ") for p in partes[1:])

    def test_marcador_de_colchetes_recua_quatro(self):
        partes = ui.wrap_ansi_line("[!] " + "abc " * 15, 24)
        assert all(p.startswith("    ") for p in partes[1:])

    def test_cores_nao_contam_na_largura(self):
        linha = f"{VERDE}{'x' * 35}{OFF}"
        assert ui.wrap_ansi_line(linha, 40) == [linha]

    def test_cores_sao_preservadas(self):
        linha = f"{VERDE} • " + "abc " * 15 + OFF
        partes = ui.wrap_ansi_line(linha, 24)
        assert partes[0].startswith(VERDE)
        assert partes[-1].endswith(OFF)

    def test_palavra_maior_que_a_linha_fica_inteira_em_linha_propria(self):
        caminho = "/" + "a" * 70
        partes = ui.wrap_ansi_line(f" • Pasta: {caminho} fim", 30)
        assert any(p.strip() == caminho for p in partes)
        # Só a palavra gigante passa do limite; o resto da linha respeita.
        assert all(ui._cell_width(p) <= 29 for p in partes if caminho not in p)

    def test_emoji_conta_duas_celulas(self):
        partes = ui.wrap_ansi_line("🎵 " * 15, 20)
        assert all(ui._cell_width(p) <= 19 for p in partes)

    def test_linha_vazia(self):
        assert ui.wrap_ansi_line("", 40) == [""]


class TestEmitWrapped:
    def test_cada_trecho_depois_de_quebra_de_linha_e_tratado(self, monkeypatch):
        saida = []
        monkeypatch.setattr(ui, "emit", lambda texto="", end="\n": saida.append(texto))
        ui.emit_wrapped("\nabc " * 20 + "fim", 20)
        assert saida[0] == ""
        assert all(ui._cell_width(p) <= 19 for p in saida)


class TestResumoLinhas:
    def test_tela_larga_alinha_os_dois_pontos(self):
        a = ui.resumo_linhas("Erros", 1, "falhas", larg_rotulo=28, cols=100)
        b = ui.resumo_linhas(
            "Capas Apple não encontradas", 2, "sem capa", larg_rotulo=28, cols=100
        )
        assert _sem_cor(a[0]).index(":") == _sem_cor(b[0]).index(":")

    def test_tela_estreita_empilha_a_explicacao(self):
        linhas = ui.resumo_linhas(
            "Erros", 1, "falhas ao consultar o Qobuz ou gravar tags", cols=40
        )
        assert _sem_cor(linhas[0]).strip() == "• Erros: 1"
        assert all(ui._cell_width(linha) <= 39 for linha in linhas)
        assert _sem_cor(linhas[1]).startswith("   falhas")

    def test_sem_explicacao_nao_gera_linha_extra(self):
        assert len(ui.resumo_linhas("Erros", 1, cols=40)) == 1


# ---------------------------------------------------------------------------
# Relatório completo do `tags`
# ---------------------------------------------------------------------------
class _Settings:
    multi_value_tags = True


class _Cliente:
    async def get_track_meta(self, track_id):
        if str(track_id) == "404":
            raise RuntimeError("não encontrada")
        return {"id": track_id, "album": {"id": "ALB1"}}

    async def get_album_meta(self, album_id):
        return {"id": album_id, "tracks": {"items": [{"id": 1}, {"id": 2}]}}


@pytest.fixture
def relatorio(monkeypatch, tmp_path):
    """Roda `retag_directory` com tudo falso e devolve as linhas impressas."""
    ids = {"a.flac": "1", "b.flac": "2", "c.flac": None, "d.mp3": "404"}
    for nome in ids:
        (tmp_path / nome).write_bytes(b"")

    def fake_flac(filename, root, final, item, album, istrack, em_image, **kw):
        return None

    async def sem_apple(item, album, sessao):
        return (None, None, "Apple desativada no teste")

    class _Sessao:
        async def aclose(self):
            pass

    monkeypatch.setattr(retro_tags.metadata, "tag_flac", fake_flac)
    monkeypatch.setattr(retro_tags, "_fetch_apple_cover_for_track", sem_apple)
    monkeypatch.setattr(retro_tags, "make_client", lambda **kw: _Sessao())
    monkeypatch.setattr(
        retro_tags, "extract_track_id", lambda p: ids.get(os.path.basename(p))
    )
    monkeypatch.setattr(retro_tags, "extract_album_id", lambda p: None)
    monkeypatch.setattr(retro_tags, "snapshot_tags", lambda p: {})

    def _rodar():
        linhas = []
        monkeypatch.setattr(
            ui, "emit", lambda texto="", end="\n": linhas.append(str(texto))
        )
        asyncio.run(retro_tags.retag_directory(str(tmp_path), _Cliente(), _Settings()))
        return [parte for linha in linhas for parte in linha.split("\n")]

    return _rodar


def test_relatorio_do_tags_cabe_em_40_colunas(colunas, relatorio):
    colunas(40)
    largas = _largas(relatorio(), 40)
    assert not largas, f"linhas acima de 40 colunas: {largas[:5]}"


def test_relatorio_do_tags_largo_mantem_dois_pontos_alinhados(colunas, relatorio):
    colunas(100)
    resumo = [
        _sem_cor(linha)
        for linha in relatorio()
        if re.match(r"^ • [^:]+: +\d+ ", _sem_cor(linha))
    ]
    assert len(resumo) >= 5
    assert len({linha.index(":") for linha in resumo}) == 1


@pytest.mark.skipif(sys.platform == "ios", reason="desnecessário no iOS")
def test_largura_do_relatorio_acompanha_a_tela(colunas):
    colunas(40)
    assert ui.report_width() == 40


# ---------------------------------------------------------------------------
# Relatório do `lyrics`
# ---------------------------------------------------------------------------
def test_relatorio_do_lyrics_cabe_em_40_colunas(colunas, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from qobuz_dl import retro_tagger as rt

    colunas(40)

    class _Engine:
        def __init__(self, genius_token=None, settings=None):
            pass

        def fetch_and_inject(self, **kwargs):
            return {"success": True}

        def close(self):
            pass

    class _ClienteLetra:
        async def get_track_meta(self, track_id):
            return {"id": track_id}

    async def buscar_letra(cli, tid, language=None):
        return None

    (tmp_path / "Artista - Faixa.flac").write_bytes(b"x")
    linhas = []
    monkeypatch.setattr(
        ui, "emit", lambda texto="", end="\n": linhas.append(str(texto))
    )
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)
    monkeypatch.setattr(rt, "extract_track_id", lambda p: "100")
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        lambda p: {
            "has_lyrics": False,
            "is_bilingual": False,
            "is_synced": False,
            "source": None,
            "content": "",
            "lrc_exists": False,
            "language": None,
        },
    )
    monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar_letra)
    monkeypatch.setattr(
        rt, "FLAC", lambda p: {"TITLE": ["Faixa"], "ARTIST": ["Artista"]}
    )
    settings = SimpleNamespace(
        lyrics_translation_lang="pt", lrc_files=True, embed_lyrics=True
    )
    asyncio.run(
        rt.process_retroactive_lyrics_async(
            str(tmp_path), _ClienteLetra(), settings=settings
        )
    )
    todas = [_sem_cor(parte) for linha in linhas for parte in linha.split("\n")]
    assert any("Resumo:" in linha for linha in todas)
    largas = _largas(todas, 40)
    assert not largas, f"linhas acima de 40 colunas: {largas[:5]}"
