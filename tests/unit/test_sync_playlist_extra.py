"""Testes adicionais de qobuz_dl/sync_playlist.py: varredura local,
busca remota paginada, limpeza de pastas e os ramos de sync_playlist
(URL inválida, playlist vazia, já sincronizada, confirmação, downloads e
exclusões). Sem rede, sem áudio real e sem lixeira real.
"""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from qobuz_dl import sync_playlist as sp
from qobuz_dl import utils

pytestmark = pytest.mark.unit

URL = "https://play.qobuz.com/playlist/123"


class _Frame:
    def __init__(self, value):
        self.text = [value]


_TAGS: dict = {}


class _FakeFLAC(dict):
    def __init__(self, path):
        if path not in _TAGS:
            raise RuntimeError("flac ilegível")
        super().__init__(_TAGS[path])


class _FakeID3(dict):
    def __init__(self, path):
        if path not in _TAGS:
            raise RuntimeError("id3 ilegível")
        super().__init__(_TAGS[path])


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    _TAGS.clear()
    monkeypatch.setattr(sp, "FLAC", _FakeFLAC)
    monkeypatch.setattr(sp, "ID3", _FakeID3)


def _arquivo(pasta, nome, tags=None):
    caminho = pasta / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"x")
    if tags is not None:
        _TAGS[str(caminho)] = tags
    return str(caminho)


def test_scan_mapeia_ids_e_separa_arquivos_sem_tag(tmp_path):
    novo = _arquivo(tmp_path, "novo.flac", {"QDL_TRACK_ID": ["1"]})
    legado = _arquivo(tmp_path, "sub/legado.flac", {"QOBUZTRACKID": ["2"]})
    mp3 = _arquivo(tmp_path, "m.mp3", {"TXXX:QDL_TRACK_ID": _Frame("3")})
    mp3_min = _arquivo(tmp_path, "n.mp3", {"TXXX:qdl_track_id": _Frame("4")})
    mp3_leg = _arquivo(tmp_path, "o.mp3", {"TXXX:QOBUZTRACKID": _Frame("5")})
    sem_tag = _arquivo(tmp_path, "sem.flac", {})
    corrompido = _arquivo(tmp_path, "ruim.mp3")
    (tmp_path / "capa.jpg").write_bytes(b"x")

    locais, sem = sp._scan_local_tracks(str(tmp_path))

    assert locais == {"1": novo, "2": legado, "3": mp3, "4": mp3_min, "5": mp3_leg}
    assert sorted(sem) == sorted([sem_tag, corrompido])


async def test_fetch_remote_junta_paginas_e_usa_primeiro_nome():
    async def get_plist_meta(_pid):
        yield {"name": "Minha", "tracks": {"items": [{"id": 1}]}}
        yield {"name": "Outro", "tracks": {"items": [{"id": 2}, {"id": 3}]}}

    nome, itens = await sp._fetch_remote_tracks(
        SimpleNamespace(get_plist_meta=get_plist_meta), "9"
    )
    assert nome == "Minha"
    assert [i["id"] for i in itens] == [1, 2, 3]


async def test_fetch_remote_sem_nome_usa_padrao():
    async def get_plist_meta(_pid):
        yield {"tracks": {"items": []}}

    nome, itens = await sp._fetch_remote_tracks(
        SimpleNamespace(get_plist_meta=get_plist_meta), "9"
    )
    assert nome == "Unknown Playlist"
    assert itens == []


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("  Rock/Pop: Hits?  ", "Rock_Pop_ Hits_"),
        ('a<b>c"d\\e|f*g', "a_b_c_d_e_f_g"),
        ("Normal", "Normal"),
    ],
)
def test_sanitize_dirname(entrada, esperado):
    assert sp._sanitize_dirname(entrada) == esperado


def test_clean_empty_dirs_remove_vazias_e_preserva_protegidas(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "_Playlists").mkdir()
    (tmp_path / "guardada").mkdir()
    (tmp_path / "cheia").mkdir()
    (tmp_path / "cheia" / "f.txt").write_text("x")

    sp._clean_empty_dirs(str(tmp_path), exclude_dirs={"guardada"})

    assert not (tmp_path / "a").exists()
    assert (tmp_path / "_Playlists").exists()
    assert (tmp_path / "guardada").exists()
    assert (tmp_path / "cheia" / "f.txt").exists()


def _app(download=None, no_m3u=True):
    chamadas = []

    async def download_from_id(tid, **kwargs):
        chamadas.append((tid, kwargs))
        return True if download is None else download(tid)

    return SimpleNamespace(
        client=object(),
        folder_format="original",
        settings=SimpleNamespace(multiple_disc_one_dir=False),
        no_m3u_for_playlists=no_m3u,
        download_from_id=download_from_id,
        chamadas=chamadas,
    )


@pytest.fixture
def ambiente(monkeypatch):
    estado = SimpleNamespace(
        nome="Lista",
        itens=[{"id": "n1", "title": "Nova", "album": {}}],
        locais={},
        m3u=[],
        lixeira=[],
    )

    async def fetch(*_a):
        return estado.nome, estado.itens

    monkeypatch.setattr(sp, "_fetch_remote_tracks", fetch)
    monkeypatch.setattr(sp, "_scan_local_tracks", lambda _d: (estado.locais, []))
    monkeypatch.setattr(utils, "make_m3u", lambda d, i=None: estado.m3u.append(d))
    monkeypatch.setattr(sp, "send2trash", estado.lixeira.append)
    return estado


async def test_url_invalida_retorna_false(tmp_path):
    assert await sp.sync_playlist(_app(), "isto nao e url", str(tmp_path)) is False


async def test_url_que_nao_e_playlist_retorna_false(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        ok = await sp.sync_playlist(
            _app(), "https://play.qobuz.com/album/abc", str(tmp_path)
        )
    assert ok is False
    assert "não é uma playlist" in caplog.text


async def test_playlist_remota_vazia_retorna_false(tmp_path, ambiente):
    ambiente.itens = []
    assert await sp.sync_playlist(_app(), URL, str(tmp_path), True) is False


async def test_ja_sincronizada_atualiza_m3u_quando_habilitado(tmp_path, ambiente):
    ambiente.locais = {"n1": str(tmp_path / "Lista" / "n1.flac")}
    app = _app(no_m3u=False)
    assert await sp.sync_playlist(app, URL, str(tmp_path), True) is True
    assert ambiente.m3u == [str(tmp_path / "Lista")]
    assert app.chamadas == []


async def test_ja_sincronizada_nao_gera_m3u_se_desabilitado(tmp_path, ambiente):
    ambiente.locais = {"n1": "x"}
    assert await sp.sync_playlist(_app(no_m3u=True), URL, str(tmp_path), True) is True
    assert ambiente.m3u == []


async def test_pasta_com_nome_da_playlist_nao_cria_subpasta(tmp_path, ambiente):
    pasta = tmp_path / "Lista"
    pasta.mkdir()
    app = _app()
    await sp.sync_playlist(app, URL, str(pasta), True)
    assert app.chamadas[0][1]["alt_path"] == str(pasta)
    assert not (pasta / "Lista").exists()


async def test_nome_da_playlist_e_sanitizado_na_subpasta(tmp_path, ambiente):
    ambiente.nome = "A/B"
    app = _app()
    await sp.sync_playlist(app, URL, str(tmp_path), True)
    assert (tmp_path / "A_B").is_dir()
    assert app.chamadas[0][1]["alt_path"] == str(tmp_path / "A_B")


@pytest.mark.parametrize("resposta", ["n", "", "s"])
async def test_confirmacao_negada_cancela_sem_baixar(
    tmp_path, ambiente, monkeypatch, resposta
):
    monkeypatch.setattr("builtins.input", lambda *_: resposta)
    app = _app()
    assert await sp.sync_playlist(app, URL, str(tmp_path), False) is False
    assert app.chamadas == []


@pytest.mark.parametrize("erro", [EOFError, KeyboardInterrupt])
async def test_interrupcao_no_prompt_cancela(tmp_path, ambiente, monkeypatch, erro):
    def levanta(*_):
        raise erro

    monkeypatch.setattr("builtins.input", levanta)
    assert await sp.sync_playlist(_app(), URL, str(tmp_path), False) is False


async def test_confirmacao_y_prossegue(tmp_path, ambiente, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: " Y ")
    app = _app()
    assert await sp.sync_playlist(app, URL, str(tmp_path), False) is True
    assert [c[0] for c in app.chamadas] == ["n1"]


async def test_download_usa_posicao_remota_e_restaura_configuracao(tmp_path, ambiente):
    ambiente.itens = [
        {"id": "a", "title": "A", "album": {}},
        {"id": "b", "title": "B", "album": {"artist": {"name": "Various Artists"}}},
        {"id": "c", "title": "C", "album": {"artist": {"name": "Banda"}}},
    ]
    ambiente.locais = {"a": "x"}
    app = _app()
    estados = []

    original = app.download_from_id

    async def espiao(tid, **kw):
        estados.append((app.folder_format, app.settings.multiple_disc_one_dir))
        return await original(tid, **kw)

    app.download_from_id = espiao
    assert await sp.sync_playlist(app, URL, str(tmp_path), True) is True

    posicoes = {tid: kw["playlist_index"] for tid, kw in app.chamadas}
    assert posicoes == {"b": 2, "c": 3}
    assert all(kw["is_playlist"] and kw["album"] is False for _, kw in app.chamadas)
    assert estados == [(".", True)] * 2
    assert app.folder_format == "original"
    assert app.settings.multiple_disc_one_dir is False


async def test_excecao_no_download_preserva_orfaos_e_restaura_config(
    tmp_path, ambiente
):
    orfao = tmp_path / "Lista" / "velho.flac"
    orfao.parent.mkdir()
    orfao.write_bytes(b"x")
    ambiente.locais = {"velho": str(orfao)}

    def explode(_tid):
        raise RuntimeError("rede caiu")

    app = _app(download=explode)
    assert await sp.sync_playlist(app, URL, str(tmp_path), True) is False
    assert orfao.exists()
    assert ambiente.lixeira == []
    assert app.folder_format == "original"


async def test_orfao_e_lrc_vao_para_a_lixeira_e_pasta_vazia_some(
    tmp_path, ambiente, monkeypatch
):
    pasta = tmp_path / "Lista"
    orfao = pasta / "Disco" / "velho.flac"
    orfao.parent.mkdir(parents=True)
    orfao.write_bytes(b"x")
    lrc = orfao.with_suffix(".lrc")
    lrc.write_text("letra")
    ambiente.locais = {"velho": str(orfao)}

    def trash_real(caminho):
        ambiente.lixeira.append(caminho)
        Path(caminho).unlink()

    monkeypatch.setattr(sp, "send2trash", trash_real)
    assert await sp.sync_playlist(_app(), URL, str(tmp_path), True) is True
    assert sorted(ambiente.lixeira) == sorted([str(orfao), str(lrc)])
    assert not (pasta / "Disco").exists()


async def test_lixeira_indisponivel_remove_diretamente(tmp_path, ambiente, monkeypatch):
    orfao = tmp_path / "Lista" / "velho.flac"
    orfao.parent.mkdir()
    orfao.write_bytes(b"x")
    ambiente.locais = {"velho": str(orfao)}

    def sem_lixeira(_p):
        raise OSError("sem lixeira")

    monkeypatch.setattr(sp, "send2trash", sem_lixeira)
    assert await sp.sync_playlist(_app(), URL, str(tmp_path), True) is True
    assert not orfao.exists()


async def test_falha_total_na_remocao_retorna_false(tmp_path, ambiente, monkeypatch):
    orfao = tmp_path / "Lista" / "velho.flac"
    orfao.parent.mkdir()
    orfao.write_bytes(b"x")
    ambiente.locais = {"velho": str(orfao)}

    def falha(_p):
        raise OSError("bloqueado")

    monkeypatch.setattr(sp, "send2trash", falha)
    monkeypatch.setattr(sp.os, "remove", falha)
    assert await sp.sync_playlist(_app(), URL, str(tmp_path), True) is False
    assert orfao.exists()


async def test_m3u_e_gerado_apos_sincronizar_quando_habilitado(tmp_path, ambiente):
    assert await sp.sync_playlist(_app(no_m3u=False), URL, str(tmp_path), True)
    assert ambiente.m3u == [str(tmp_path / "Lista")]
