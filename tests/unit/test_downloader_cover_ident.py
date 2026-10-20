"""Capas em playlist/lote: viram notas curtas na linha "Em Progresso".

Em playlist/lote várias faixas rodam ao mesmo tempo, e linhas de capa próprias
se misturariam com as das outras faixas. Com `notas` (lista), a função não
escreve linhas de capa: acrescenta notas ("capa Apple (HQ)", "capa já
baixada", "sem capa") e o chamador as põe na linha de progresso da faixa.
Sem `notas` (álbum e faixa avulsa) as linhas continuam como sempre.
"""

import pytest

from qobuz_dl import downloader, ui

pytestmark = pytest.mark.unit


@pytest.fixture
def saida(monkeypatch):
    linhas = []

    def _sem_terminal(fd=None):
        raise OSError("sem terminal")

    monkeypatch.setattr(ui.os, "get_terminal_size", _sem_terminal)
    monkeypatch.setenv("COLUMNS", "100")
    ui.configure(color=False, unicode=True)
    monkeypatch.setattr(ui, "_write_locked", lambda texto, end: linhas.append(str(texto)))
    return linhas


@pytest.fixture
def fontes(monkeypatch):
    """Apple achando (ou não) a capa; Qobuz devolve bytes (ou nada)."""
    estado = {"apple": True, "qobuz": True}

    async def fake_apple(*args, **kwargs):
        return b"APPLE" if estado["apple"] else None

    async def fake_qobuz(*args, **kwargs):
        return b"QOBUZ" if estado["qobuz"] else None

    monkeypatch.setattr(downloader, "_try_apple_cover_bytes", fake_apple)
    monkeypatch.setattr(downloader, "_fetch_qobuz_cover_bytes", fake_qobuz)
    monkeypatch.setattr(downloader, "_resolve_art_url", lambda item, size: "mesma-url")
    return estado


async def _capas(tmp_path, *, salvar=True, notas=None):
    return await downloader._get_cover_and_embed(
        {},
        str(tmp_path),
        save_cover=salvar,
        embed_art=True,
        saved_name="cover.jpg",
        embed_name=".embed_123.jpg",
        saved_art_size="large",
        embedded_art_size="large",
        notas=notas,
    )


# ------------------------------------------------ sem notas: álbum / faixa avulsa
async def test_sem_notas_mantem_as_linhas_de_capa(tmp_path, saida, fontes):
    await _capas(tmp_path)
    assert saida == [
        "[+] Capa salva   cover.jpg · Apple (HQ)",
        "[+] Capa de embed   .embed_123.jpg · Apple (HQ)",
    ]


async def test_sem_notas_reaproveitar_cover_nao_usa_emoji(tmp_path, saida, fontes):
    fontes["apple"] = False  # fallback da Qobuz, com a mesma URL
    await _capas(tmp_path)
    assert saida[-1] == "[+] Capa de embed   reaproveitada de cover.jpg"
    assert "🌁" not in "".join(saida)


# ------------------------------------------------ com notas: playlist / lote
async def test_com_notas_nao_escreve_linhas_de_capa(tmp_path, saida, fontes):
    notas = []
    await _capas(tmp_path, salvar=False, notas=notas)
    assert saida == []
    assert notas == ["capa Apple (HQ)"]


async def test_com_notas_capa_salva_e_embed_geram_a_mesma_nota(tmp_path, saida, fontes):
    notas = []
    await _capas(tmp_path, notas=notas)
    assert saida == []
    assert set(notas) == {"capa Apple (HQ)"}


async def test_com_notas_fallback_qobuz(tmp_path, saida, fontes):
    fontes["apple"] = False
    notas = []
    await _capas(tmp_path, notas=notas)
    assert saida == []
    assert set(notas) == {"capa Qobuz"}


async def test_com_notas_embed_ja_baixado(tmp_path, saida, fontes):
    (tmp_path / ".embed_123.jpg").write_bytes(b"x")
    notas = []
    await _capas(tmp_path, salvar=False, notas=notas)
    assert saida == []
    assert notas == ["capa já baixada"]


async def test_com_notas_sem_nenhuma_fonte(tmp_path, saida, fontes):
    fontes["apple"] = False
    fontes["qobuz"] = False
    notas = []
    await _capas(tmp_path, salvar=False, notas=notas)
    assert saida == []
    assert notas == ["sem capa"]


# ------------------------------------------------ cauda da linha "Em Progresso"
def test_progress_tail_junta_tamanho_e_nota(monkeypatch):
    ui.configure(unicode=True)
    monkeypatch.setitem(downloader._PROGRESS_NOTES, "[01] Faixa", "capa Apple (HQ)")
    assert downloader._progress_tail("[01] Faixa", 32.4) == "32.4 MB · capa Apple (HQ)"
    assert downloader._progress_tail("[01] Faixa") == "capa Apple (HQ)"


def test_progress_tail_sem_nota_so_tem_o_tamanho():
    ui.configure(unicode=True)
    assert downloader._progress_tail("[99] Sem nota", 10.0) == "10.0 MB"
    assert downloader._progress_tail("[99] Sem nota") == ""
