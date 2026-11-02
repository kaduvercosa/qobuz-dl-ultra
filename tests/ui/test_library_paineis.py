"""Telas do `library`/`scan`/`sync-favorites`: cada bloco é um painel (caixa).

A lógica está em `test_library_cmd.py`; aqui se confere o desenho: títulos na
borda, nenhuma linha acima da largura do terminal, os dados nos painéis certos
e as listagens (`list`, `missing`, `history`) continuando em uma linha por
álbum, boas para `grep`.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest

from qobuz_dl import library_cmd as lc
from qobuz_dl import library_scan as ls
from qobuz_dl import ui
from qobuz_dl.library_db import LibraryDB

pytestmark = pytest.mark.ui

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
LARGURAS = [40, 80, 120]


@pytest.fixture(autouse=True)
def sem_mutagen(monkeypatch):
    monkeypatch.setattr(ls, "_read_tags", lambda p: {})


@pytest.fixture(autouse=True)
def sem_terminal_interativo(monkeypatch):
    """Nenhum teste daqui abre o prompt de verdade.

    No a-Shell `isatty()` pode dar True mesmo sob o pytest, e o `PromptSession`
    derruba o runtime iOS (segfault). Por isso `_interactive` fica falso e
    `_ask` falha alto se alguém chegar nele sem ter combinado a resposta antes.
    """
    monkeypatch.setattr(lc, "_interactive", lambda: False)

    async def proibido(prompt):
        raise AssertionError(f"prompt inesperado: {prompt!r}")

    monkeypatch.setattr(lc, "_ask", proibido)


@pytest.fixture
def lib(tmp_path):
    return LibraryDB(tmp_path / "l.db")


@pytest.fixture
def saida(monkeypatch):
    blocos = []

    def captura(texto="", end="\n"):
        blocos.append(texto)

    monkeypatch.setattr(ui, "emit", captura)
    monkeypatch.setattr(ui, "emit_always", captura)
    monkeypatch.setattr(ui, "raw_width", lambda *a, **k: 100)
    return blocos


def _texto(blocos):
    return _ANSI.sub("", "\n".join(str(b) for b in blocos))


def _titulos(blocos):
    return [l for l in _texto(blocos).splitlines() if l.startswith("╭")]


def _args(**kw):
    base = dict(
        DIR=None,
        dry_run=False,
        no_sentinel=False,
        no_adopt=False,
        no_review=True,
        rescan=False,
        max_depth=4,
        fuzzy_threshold=0.85,
        json=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _library(lib, tmp_path, **kw):
    ns = SimpleNamespace(TARGET=None, limit=None, fix=False, dry_run=False, **kw)
    return asyncio.run(lc.cmd_library(ns, directory=str(tmp_path), lib=lib))


def _pasta(root, rel, n=1):
    p = root / rel
    p.mkdir(parents=True)
    for i in range(n):
        (p / f"{i}.flac").write_bytes(b"x")
    return p


def test_scan_mostra_cabecalho_e_resultado_em_paineis(tmp_path, lib, saida):
    lib.upsert_album("qobuz", "1", "B", "A")
    _pasta(tmp_path / "m", "A - B")
    asyncio.run(lc.cmd_scan(_args(DIR=str(tmp_path / "m")), directory="x", lib=lib))
    titulos = _titulos(saida)
    assert "SCAN DA BIBLIOTECA" in titulos[0]
    assert "RESULTADO" in titulos[-1]
    texto = _texto(saida)
    assert "PASTAS ANALISADAS" in texto and "MODO" in texto


def test_scan_com_catalogo_vazio_avisa_dentro_do_cabecalho(tmp_path, lib, saida):
    _pasta(tmp_path / "m", "A - B")
    asyncio.run(lc.cmd_scan(_args(DIR=str(tmp_path / "m")), directory="x", lib=lib))
    cabecalho = _texto(saida).split("RESULTADO")[0]
    assert "catálogo está vazio" in " ".join(cabecalho.split())


def test_status_vazio_vira_painel_com_aviso(tmp_path, lib, saida):
    assert _library(lib, tmp_path, action="status") == 0
    assert "BIBLIOTECA" in _titulos(saida)[0]
    assert "Catálogo vazio" in _texto(saida)


def test_status_lista_contagens_e_albuns_presos(tmp_path, lib, saida):
    a = lib.upsert_album("qobuz", "1", "B", "A")
    lib.update_status(a, "downloading")
    assert _library(lib, tmp_path, action="status") == 0
    texto = " ".join(_texto(saida).split())
    assert "QOBUZ / DOWNLOADING" in texto
    assert "presos em queued/downloading" in texto
    assert len(_titulos(saida)) == 1


def test_reconcile_vira_painel_e_sugere_fix(tmp_path, lib, saida):
    a = lib.upsert_album("qobuz", "1", "B", "A")
    pasta = _pasta(tmp_path, "A - B")
    ls.mark_album_downloaded(lib, a, folder=pasta)
    import shutil

    shutil.rmtree(pasta)
    ns = SimpleNamespace(action="reconcile", TARGET=str(tmp_path), limit=None)
    ns.fix, ns.dry_run = False, False
    asyncio.run(lc.cmd_library(ns, directory="x", lib=lib))
    assert "RECONCILIAÇÃO" in _titulos(saida)[0]
    texto = " ".join(_texto(saida).split())
    assert "Sumiu do disco: A - B" in texto
    assert "Use --fix" in texto


def test_listagens_continuam_uma_linha_por_album(tmp_path, lib, saida):
    lib.upsert_album("qobuz", "1", "B", "A")
    lib.upsert_album("qobuz", "2", "D", "C")
    assert _library(lib, tmp_path, action="list") == 0
    linhas = [l for l in _texto(saida).splitlines() if "not_downloaded" in l]
    assert len(linhas) == 2
    assert not any(l.startswith(("╭", "│")) for l in linhas)


class _Qobuz:
    def __init__(self, itens):
        self.downloads_db = None
        self.settings = SimpleNamespace(write_sentinel=True)
        self.baixados = []

        class C:
            async def get_all_favorites(self, fav_type, *, page_size, max_pages):
                return itens, len(itens)

        self.client = C()

    async def download_from_id(self, album_id, album=True):
        self.baixados.append(album_id)
        return True


def _sf(**kw):
    base = dict(
        download_new=False,
        download_missing=False,
        dry_run=False,
        yes=True,
        every=None,
        limit=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _favoritos(n):
    return [
        {"id": str(i), "title": f"Álbum {i}", "artist": {"name": "Artista"}}
        for i in range(1, n + 1)
    ]


def test_sync_mostra_paineis_de_resumo_novos_e_download(lib, saida):
    q = _Qobuz(_favoritos(3))
    rc = asyncio.run(lc.cmd_sync_favorites(_sf(download_new=True), q, lib=lib))
    assert rc == 0
    titulos = _titulos(saida)
    assert "SINCRONIZAÇÃO DE FAVORITOS" in titulos[0]
    assert "ÁLBUNS NOVOS ADICIONADOS (3)" in titulos[1]
    assert "RESUMO DO DOWNLOAD" in titulos[2]
    assert "Artista - Álbum 1" in _texto(saida)


def test_sync_corta_a_lista_de_novos_e_diz_quantos_faltam(lib, saida):
    q = _Qobuz(_favoritos(lc._LISTA_MAX + 5))
    asyncio.run(lc.cmd_sync_favorites(_sf(), q, lib=lib))
    texto = " ".join(_texto(saida).split())
    assert "... e mais 5 álbuns." in texto
    assert f"Artista - Álbum {lc._LISTA_MAX}" in texto
    assert f"Artista - Álbum {lc._LISTA_MAX + 1} " not in texto


def test_sync_simulacao_avisa_que_nada_foi_gravado(lib, saida):
    q = _Qobuz(_favoritos(1))
    asyncio.run(lc.cmd_sync_favorites(_sf(dry_run=True), q, lib=lib))
    assert "SIMULAÇÃO" in _titulos(saida)[-1]
    assert "nenhuma alteração foi gravada" in " ".join(_texto(saida).split())


def test_sync_menu_interativo_e_um_painel_com_as_tres_opcoes(lib, saida, monkeypatch):
    q = _Qobuz(_favoritos(2))
    monkeypatch.setattr(lc, "_interactive", lambda: True)

    async def sair(prompt):
        return "3"

    monkeypatch.setattr(lc, "_ask", sair)
    asyncio.run(lc.cmd_sync_favorites(_sf(), q, lib=lib))
    assert "OPÇÕES DE DOWNLOAD DOS FAVORITOS" in _titulos(saida)[-1]
    texto = " ".join(_texto(saida).split())
    assert "[1] Baixar apenas os 2 álbuns NOVOS" in texto
    assert "[3] Apenas sincronizar o catálogo e sair [Padrão]" in texto
    assert q.baixados == []


@pytest.mark.parametrize("largura", LARGURAS)
def test_nenhuma_linha_passa_da_largura_do_terminal(largura, lib, saida, monkeypatch):
    monkeypatch.setattr(ui, "raw_width", lambda *a, **k: largura)
    itens = [
        {"id": "1", "title": "宇多田ヒカル " * 8, "artist": {"name": "Daft Punk 🎧"}},
        {"id": "2", "title": "x" * 150, "artist": {"name": "A"}},
    ]
    asyncio.run(lc.cmd_sync_favorites(_sf(), _Qobuz(itens), lib=lib))
    for linha in _texto(saida).splitlines():
        assert ui._cell_width(linha) <= ui.width(), repr(linha)
