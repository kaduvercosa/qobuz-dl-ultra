"""Playlist em pasta única: faixas com MESMO artista e título, álbuns diferentes.

Exercita o `Download._download_and_tag` real (só rede, tag e disco são
simulados) para travar o comportamento pedido:

* cada faixa é identificada pelo ID Qobuz, não pelo nome do arquivo;
* nenhuma é "pulada como já existente" por engano nem sobrescrita;
* vale no modo sequencial E no paralelo (asyncio.gather);
* re-executar não baixa nada de novo nem cria duplicatas;
* falha/cancelamento liberam o nome reservado.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader
from qobuz_dl import track_identity as ti

pytestmark = pytest.mark.unit


async def _get_format(*args, **kwargs):
    return ("flac", True, 16, 44100)


async def _no_sleep(*args, **kwargs):
    return None


def _make_obj(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        legacy_charmap=False,
        multiple_disc_one_dir=True,
        multiple_disc_prefix="Disc",
        multiple_disc_track_format="{track_number}",
        verify_after_download=False,
        delay=0,
        segment_workers=None,
        embed_lyrics=True,
        lyrics_translation_lang="pt",
    )
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda *a, **k: object())

    obj = downloader.Download(
        SimpleNamespace(),
        "x",
        str(tmp_path),
        6,
        settings=settings,
        is_playlist=True,
    )
    obj.track_format = "{track_number} - {track_title}"
    obj.embed_art = False
    obj.http_session = object()
    obj.is_playlist = True
    obj.playlist_as_albums = False

    async def fresh_url(*args, **kwargs):
        return {"url": "https://example.test/audio"}

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(obj, "_get_format", _get_format)
    monkeypatch.setattr(downloader.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(downloader, "emit_progress_json", lambda *a, **k: None)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)
    return obj


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Simula download e tag; o 'ID gravado na tag' é o conteúdo 'id:<n>'."""
    ti._claims.clear()
    baixados = []

    async def fake_download(url, filename, desc, **kwargs):
        baixados.append(Path(filename).name)
        # Cede o controle de verdade (asyncio.sleep está neutralizado acima)
        # para que as tarefas do modo paralelo realmente se intercalem.
        await asyncio.get_running_loop().run_in_executor(None, time.sleep, 0.02)
        Path(filename).write_bytes(b"audio")

    def fake_tag(src, root, final, track_metadata, *args, **kwargs):
        Path(final).write_text(f"id:{track_metadata['id']}", encoding="utf-8")

    def fake_read(path):
        try:
            texto = Path(path).read_text(encoding="utf-8")
        except OSError:
            return None
        return texto[3:] if texto.startswith("id:") else None

    monkeypatch.setattr(downloader, "tqdm_download", fake_download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", fake_tag)
    monkeypatch.setattr(ti, "read_track_id", fake_read)

    yield SimpleNamespace(baixados=baixados, pasta=tmp_path)
    ti._claims.clear()


def _faixa(track_id, album, titulo="Song", artista="Artist"):
    return {
        "id": track_id,
        "title": titulo,
        "track_number": 1,
        "media_number": 1,
        "performer": {"name": artista},
        "album": {"title": album, "id": f"alb-{track_id}", "artist": {"name": artista}},
    }


async def _baixa(obj, pasta, meta):
    return await obj._download_and_tag(
        str(pasta), int(meta["id"]), {"url": "x"}, meta, meta, True, False
    )


def _arquivos(pasta):
    return {p.name: p.read_text(encoding="utf-8") for p in pasta.glob("*.flac")}


async def test_sequencial_mesmo_artista_e_titulo_albuns_diferentes(
    ambiente, monkeypatch, tmp_path
):
    obj = _make_obj(tmp_path, monkeypatch)
    assert await _baixa(obj, tmp_path, _faixa(1, "Album A")) is True
    assert await _baixa(obj, tmp_path, _faixa(2, "Album B")) is True

    assert _arquivos(tmp_path) == {
        "Artist - Song.flac": "id:1",
        "Artist - Song (Album B).flac": "id:2",
    }
    assert len(ambiente.baixados) == 2


async def test_paralelo_nao_sobrescreve_nem_pula(ambiente, monkeypatch, tmp_path):
    metas = [_faixa(i, f"Album {i}") for i in range(1, 6)]
    objs = [_make_obj(tmp_path, monkeypatch) for _ in metas]

    resultados = await asyncio.gather(
        *(_baixa(o, tmp_path, m) for o, m in zip(objs, metas))
    )

    assert resultados == [True] * 5
    arquivos = _arquivos(tmp_path)
    assert len(arquivos) == 5, f"faltam faixas: {sorted(arquivos)}"
    assert sorted(arquivos.values()) == [f"id:{i}" for i in range(1, 6)]
    assert len(ambiente.baixados) == 5
    assert ti._claims == {}, "reservas de nome não foram liberadas"


async def test_mesmo_album_e_mesmo_titulo_usa_id_no_nome(
    ambiente, monkeypatch, tmp_path
):
    """Duas versões no mesmo álbum com título/artista iguais."""
    metas = [_faixa(i, "Mesmo Album") for i in (10, 11, 12)]
    objs = [_make_obj(tmp_path, monkeypatch) for _ in metas]
    resultados = await asyncio.gather(
        *(_baixa(o, tmp_path, m) for o, m in zip(objs, metas))
    )
    assert resultados == [True] * 3
    assert sorted(_arquivos(tmp_path).values()) == ["id:10", "id:11", "id:12"]


async def test_reexecucao_nao_baixa_de_novo_nem_duplica(
    ambiente, monkeypatch, tmp_path
):
    metas = [_faixa(i, f"Album {i}") for i in (1, 2, 3)]
    for m in metas:
        assert await _baixa(_make_obj(tmp_path, monkeypatch), tmp_path, m) is True
    antes = _arquivos(tmp_path)
    assert len(ambiente.baixados) == 3

    # Segunda execução, agora em paralelo e em ordem inversa.
    segunda = await asyncio.gather(
        *(
            _baixa(_make_obj(tmp_path, monkeypatch), tmp_path, m)
            for m in reversed(metas)
        )
    )
    assert segunda == [True] * 3
    assert len(ambiente.baixados) == 3, "re-baixou faixas que já existiam"
    assert _arquivos(tmp_path) == antes


async def test_mesma_faixa_repetida_na_playlist_baixa_uma_vez(
    ambiente, monkeypatch, tmp_path
):
    meta = _faixa(7, "Album")
    objs = [_make_obj(tmp_path, monkeypatch) for _ in range(2)]
    resultados = await asyncio.gather(*(_baixa(o, tmp_path, meta) for o in objs))
    assert resultados == [True, True]
    assert list(_arquivos(tmp_path).values()) == ["id:7"]
    assert len(ambiente.baixados) == 1


async def test_falha_libera_o_nome_reservado(ambiente, monkeypatch, tmp_path):
    def tag_que_falha(*args, **kwargs):
        raise RuntimeError("falha ao gravar tag")

    obj = _make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.metadata, "tag_flac", tag_que_falha)
    assert await _baixa(obj, tmp_path, _faixa(1, "Album A")) is False
    assert ti._claims == {}

    # O mesmo nome precisa estar livre para a próxima faixa.
    def tag_ok(src, root, final, track_metadata, *a, **k):
        Path(final).write_text(f"id:{track_metadata['id']}", encoding="utf-8")

    monkeypatch.setattr(downloader.metadata, "tag_flac", tag_ok)
    assert (
        await _baixa(_make_obj(tmp_path, monkeypatch), tmp_path, _faixa(2, "B")) is True
    )
    assert "Artist - Song.flac" in _arquivos(tmp_path)


async def test_modo_playlist_as_albums_nao_e_afetado(ambiente, monkeypatch, tmp_path):
    """Fora da pasta única o nome segue o track_format; nada é reservado."""
    obj = _make_obj(tmp_path, monkeypatch)
    obj.playlist_as_albums = True
    assert await _baixa(obj, tmp_path, _faixa(1, "Album A")) is True
    assert "01 - Song.flac" in _arquivos(tmp_path)
    assert ti._claims == {}
