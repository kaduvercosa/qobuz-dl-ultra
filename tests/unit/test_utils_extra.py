"""Testes adicionais de qobuz_dl/utils.py: m3u, filtro de discografia,
binários externos, integridade de áudio, URLs, classificação de release,
nomes de arquivo, capa Apple e caminhos de configuração.
Sem rede, sem ffmpeg real e sem áudio real.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from qobuz_dl import utils

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _cache_limpo(monkeypatch):
    monkeypatch.setattr(utils, "_BINARIOS_CHECADOS", {})


def test_partial_formatter_campos_ausentes_e_formato_invalido():
    f = utils.PartialFormatter()
    assert f.format("{a}-{b}", a="x") == "x-n/a"
    assert f.format("{a}", a="") == "n/a"
    assert f.format("{n:d}", n="abc") == "n/a"
    assert utils.PartialFormatter(bad_fmt=None).format("{n}", n=3) == "3"
    with pytest.raises(ValueError):
        utils.PartialFormatter(bad_fmt=None).format("{n:d}", n="abc")


def _audio(pasta, nome):
    p = pasta / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_make_m3u_ordem_natural_sem_remote(tmp_path):
    for n in ("Faixa 10.flac", "Faixa 2.flac", "Faixa 1.mp3", "capa.jpg"):
        _audio(tmp_path, n)
    utils.make_m3u(str(tmp_path))
    linhas = (tmp_path / f"{tmp_path.name}.m3u8").read_text("utf-8").splitlines()
    assert linhas[0] == "#EXTM3U"
    faixas = [x for x in linhas if not x.startswith("#")]
    assert faixas == ["Faixa 1.mp3", "Faixa 2.flac", "Faixa 10.flac"]
    assert "capa.jpg" not in "\n".join(linhas)


def test_make_m3u_sem_audio_nao_cria_arquivo(tmp_path):
    (tmp_path / "x.txt").write_text("a")
    utils.make_m3u(str(tmp_path))
    assert not (tmp_path / f"{tmp_path.name}.m3u8").exists()


def test_make_m3u_ordem_remota_por_titulo_e_nome_de_arquivo(tmp_path):
    _audio(tmp_path, "01 Alfa.flac")
    _audio(tmp_path, "02 Beta.flac")
    remotos = [
        {"id": 2, "title": "Beta"},
        {"id": 1, "title": "Alfa"},
    ]
    utils.make_m3u(str(tmp_path), remotos)
    linhas = (tmp_path / f"{tmp_path.name}.m3u8").read_text("utf-8").splitlines()
    faixas = [x for x in linhas if not x.startswith("#")]
    assert faixas == ["02 Beta.flac", "01 Alfa.flac"]


def test_make_m3u_registra_faixas_faltando(tmp_path, caplog):
    _audio(tmp_path, "01 Alfa.flac")
    remotos = [
        {"id": 1, "title": "Alfa"},
        {
            "id": 9,
            "title": "Inexistente",
            "performer": {"name": "Solo"},
            "album": {"artist": {"name": "Various Artists"}},
        },
        {"id": 10, "title": "Outra", "album": {"artist": {"name": "Banda"}}},
    ]
    with caplog.at_level("WARNING"):
        utils.make_m3u(str(tmp_path), remotos)
    assert "MISSING LOCAL TRACKS" in caplog.text
    assert "Inexistente" in caplog.text
    assert "Outra" in caplog.text


def test_make_m3u_remoto_sem_nenhum_casamento_cai_na_ordem_natural(tmp_path):
    _audio(tmp_path, "b.flac")
    _audio(tmp_path, "a.flac")
    utils.make_m3u(str(tmp_path), [{"id": 1, "title": "Unknown Title"}])
    linhas = (tmp_path / f"{tmp_path.name}.m3u8").read_text("utf-8").splitlines()
    assert [x for x in linhas if not x.startswith("#")] == ["a.flac", "b.flac"]


def test_make_m3u_usa_tags_flac_e_id3(tmp_path, monkeypatch):
    import mutagen
    import mutagen.flac
    import mutagen.id3

    _audio(tmp_path, "x.flac")
    _audio(tmp_path, "y.mp3")

    class FakeFLAC(dict):
        def __init__(self, _p):
            super().__init__(
                QOBUZTRACKID=["77"], ISRC=["ISRC1"], TITLE=["Tit"], ARTIST=["Art"]
            )

    class Frame:
        def __init__(self, text, desc=""):
            self.text = [text]
            self.desc = desc

    class FakeID3(dict):
        def __init__(self, _p):
            super().__init__(
                TSRC=Frame("ISRC2"), TIT2=Frame("Mp3 Tit"), TPE1=Frame("Mp3 Art")
            )

        def getall(self, _k):
            return [Frame("55", "other"), Frame("88", "qobuztrackid")]

    class FakeFile:
        info = SimpleNamespace(length=123.7)

    monkeypatch.setattr(mutagen, "File", lambda _p: FakeFile())
    monkeypatch.setattr(mutagen.flac, "FLAC", FakeFLAC)
    monkeypatch.setattr(mutagen.id3, "ID3", FakeID3)

    utils.make_m3u(str(tmp_path), [{"id": 88, "title": "z"}, {"id": 77, "title": "z"}])
    texto = (tmp_path / f"{tmp_path.name}.m3u8").read_text("utf-8")
    assert "#EXTINF:123, Mp3 Art - Mp3 Tit\ny.mp3" in texto
    assert "#EXTINF:123, Art - Tit\nx.flac" in texto
    assert texto.index("y.mp3") < texto.index("x.flac")


def _alb(titulo, bit, taxa, artista="Banda", versao="", id_=None):
    return {
        "id": id_ or titulo + versao,
        "title": titulo,
        "version": versao,
        "maximum_bit_depth": bit,
        "maximum_sampling_rate": taxa,
        "artist": {"name": artista},
    }


def _contents(*albuns):
    return [{"name": "Banda", "albums": {"items": list(albuns)}}]


def test_discografia_mantem_melhor_qualidade_e_remove_outros_artistas():
    r = utils.smart_discography_filter(
        _contents(
            _alb("Disco", 16, 44.1),
            _alb("Disco (Deluxe)", 24, 96),
            _alb("Tributo", 16, 44.1, artista="Outro"),
        )
    )
    assert [a["title"] for a in r] == ["Disco (Deluxe)"]


def test_discografia_save_space_prefere_menor_taxa():
    r = utils.smart_discography_filter(
        _contents(_alb("Disco", 24, 192, id_="a"), _alb("Disco", 24, 96, id_="b")),
        save_space=True,
    )
    assert [a["id"] for a in r] == ["b"]


def test_discografia_prefere_remaster_e_pula_extras():
    r = utils.smart_discography_filter(
        _contents(
            _alb("Disco", 24, 96, id_="orig"),
            _alb("Disco", 24, 96, versao="Remastered", id_="rem"),
        )
    )
    assert [a["id"] for a in r] == ["rem"]
    r = utils.smart_discography_filter(
        _contents(_alb("Disco Live", 16, 44.1), _alb("Outro", 16, 44.1)),
        skip_extras=True,
    )
    assert [a["title"] for a in r] == ["Outro"]


def test_format_duration():
    assert utils.format_duration(3725) == "01:02:05"
    assert utils.format_duration(59) == "00:00:59"


def test_encontrar_binario_usa_cache_e_dirs_extra(tmp_path, monkeypatch):
    chamadas = []

    def which(nome, path=None):
        chamadas.append((nome, path))
        return "/x/ffmpeg" if path == str(tmp_path) else None

    monkeypatch.setattr(utils.shutil, "which", which)
    monkeypatch.setattr(utils, "_DIRS_EXTRA", ["", "/nao/existe", str(tmp_path)])
    assert utils.encontrar_binario("ffmpeg") == "/x/ffmpeg"
    assert utils.encontrar_binario("ffmpeg") == "/x/ffmpeg"
    assert len(chamadas) == 2


def test_encontrar_binario_ausente(monkeypatch):
    monkeypatch.setattr(utils.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(utils, "_DIRS_EXTRA", [])
    assert utils.encontrar_binario("fpcalc") is None


def test_checar_binarios_externos(monkeypatch):
    avisos = []
    monkeypatch.setattr(utils, "_avisar", lambda t, d: avisos.append(t))
    monkeypatch.setattr(utils, "encontrar_binario", lambda _n: None)
    r = utils.checar_binarios_externos(precisa_fpcalc=True)
    assert r == {"ffmpeg": None, "fpcalc": None}
    assert avisos == ["ffmpeg nao encontrado", "fpcalc nao encontrado"]

    avisos.clear()
    monkeypatch.setattr(utils, "encontrar_binario", lambda n: f"/bin/{n}")
    r = utils.checar_binarios_externos()
    assert r == {"ffmpeg": "/bin/ffmpeg", "fpcalc": None}
    assert avisos == []


def test_avisar_usa_ui(monkeypatch):
    from qobuz_dl import ui

    saida = []
    monkeypatch.setattr(ui, "warn", lambda t: saida.append(("w", t)))
    monkeypatch.setattr(ui, "wrapped", lambda d, indent=0: saida.append(("p", d)))
    utils._avisar("titulo", "detalhe")
    assert saida == [("w", "titulo"), ("p", "detalhe")]


def test_verify_audio_arquivo_inexistente(tmp_path):
    ok, msg = utils.verify_audio_integrity(str(tmp_path / "x.flac"))
    assert ok is False
    assert "nao encontrado" in msg


def test_verify_audio_sem_ffmpeg(tmp_path, monkeypatch):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    monkeypatch.setattr(utils, "encontrar_binario", lambda _n: None)
    ok, msg = utils.verify_audio_integrity(str(f))
    assert not ok
    assert "ffmpeg nao disponivel" in msg


@pytest.mark.parametrize(
    "retorno,esperado_ok,trecho",
    [
        (SimpleNamespace(returncode=0, stderr=""), True, ""),
        (SimpleNamespace(returncode=0, stderr="  "), True, ""),
        (SimpleNamespace(returncode=1, stderr="corrompido\n"), False, "corrompido"),
        (SimpleNamespace(returncode=2, stderr=""), False, "codigo 2"),
        (SimpleNamespace(returncode=0, stderr="aviso"), False, "aviso"),
    ],
)
def test_verify_audio_resultados(tmp_path, monkeypatch, retorno, esperado_ok, trecho):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    monkeypatch.setattr(utils, "encontrar_binario", lambda _n: "/bin/ffmpeg")
    monkeypatch.setattr(utils.subprocess, "run", lambda *_a, **_k: retorno)
    ok, msg = utils.verify_audio_integrity(str(f))
    assert ok is esperado_ok
    assert trecho in msg


@pytest.mark.parametrize(
    "erro,trecho",
    [
        (FileNotFoundError(), "ffmpeg nao encontrado"),
        (subprocess.TimeoutExpired("ffmpeg", 5), "5s"),
    ],
)
def test_verify_audio_excecoes(tmp_path, monkeypatch, erro, trecho):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    monkeypatch.setattr(utils, "encontrar_binario", lambda _n: "/bin/ffmpeg")

    def explode(*_a, **_k):
        raise erro

    monkeypatch.setattr(utils.subprocess, "run", explode)
    ok, msg = utils.verify_audio_integrity(str(f), timeout=5)
    assert not ok
    assert trecho in msg


def test_create_and_return_dir(tmp_path, monkeypatch):
    destino = tmp_path / "a" / "b"
    assert utils.create_and_return_dir(str(destino)) == str(destino)
    assert destino.is_dir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert utils.create_and_return_dir("~/musica") == str(tmp_path / "musica")


@pytest.mark.parametrize(
    "url,esperado",
    [
        ("https://www.qobuz.com/us-en/album/nome-do-album/abc123", ("album", "abc123")),
        ("https://open.qobuz.com/track/42", ("track", "42")),
        ("https://play.qobuz.com/playlist/777", ("playlist", "777")),
        ("/us-en/artist/-/55", ("artist", "55")),
        ("https://play.qobuz.com/label/9", ("label", "9")),
    ],
)
def test_get_url_info(url, esperado):
    assert utils.get_url_info(url) == esperado


def test_get_url_info_invalida():
    with pytest.raises(AttributeError):
        utils.get_url_info("https://exemplo.com/nada")


def test_get_album_artist_variantes():
    assert utils.get_album_artist({"artist": {"name": "Solo"}}) == ["Solo"]
    assert utils.get_album_artist({}) == []
    album = {
        "artist": {"name": "Fallback"},
        "artists": [
            {"name": "A", "roles": ["main-artist"]},
            {"name": "P", "roles": ["producer"]},
            {"name": "B", "roles": ["main-artist", "featured-artist"]},
        ],
    }
    assert utils.get_album_artist(album) == ["A", "B"]
    sem_main = {"artist": {"name": "F"}, "artists": [{"name": "P", "roles": []}]}
    assert utils.get_album_artist(sem_main) == ["F"]
    sem_main_sem_artist = {"artists": [{"name": "P", "roles": []}]}
    assert utils.get_album_artist(sem_main_sem_artist) == []


def test_get_album_artist_estrutura_quebrada_cai_no_fallback(caplog):
    album = {"artist": {"name": "F"}, "artists": [{"roles": ["main-artist"]}]}
    with caplog.at_level("ERROR"):
        assert utils.get_album_artist(album) == ["F"]
    assert "Error getting album artist" in caplog.text


@pytest.mark.parametrize(
    "kwargs,esperado",
    [
        ({"version": "Live at X"}, "live"),
        ({"title": "Show (Live)"}, "live"),
        ({"title": "Show - Live"}, "live"),
        ({"title": "Best Of Banda"}, "compilation"),
        ({"version": "Anthology"}, "compilation"),
        ({"title": "Algo EP"}, "ep"),
        ({"version": "EP"}, "ep"),
        ({"track_count": 1}, "single"),
        ({"track_count": 3}, "single"),
        ({"track_count": 4}, "ep"),
        ({"track_count": 7}, "ep"),
        ({"track_count": 8}, "album"),
        ({"duration_seconds": 1800}, "album"),
        ({"api_release_type": "Single"}, "single"),
        ({"item_type": "track"}, "track"),
        ({}, "unknown"),
        ({"track_count": None, "duration_seconds": None}, "unknown"),
    ],
)
def test_classify_release_type(kwargs, esperado):
    assert utils.classify_release_type(**kwargs) == esperado


def test_apply_legacy_charmap():
    assert utils.apply_legacy_charmap("A: B?") == "A- B"
    assert utils.apply_legacy_charmap('x/y\\z*"w"<a>|b') == "x-y-z-'w'[a]-b"
    assert utils.apply_legacy_charmap("A / B") == "A - B"


def test_clean_filename_unicode_e_pontuacao():
    assert utils.clean_filename("A:B") == "A：B"
    assert utils.clean_filename("A:B", legacy_charmap=True) == "A-B"
    assert utils.clean_filename("  Nome...  ") == "Nome"
    assert utils.clean_filename("Disco ()") == "Disco"
    assert utils.clean_filename("Disco [ ]") == "Disco"
    assert utils.clean_filename("a  ,, b") == "a, b"
    assert utils.clean_filename("Cafe\u0301") == "Caf\u00e9"


def test_invalid_chars_to_fullwidth():
    assert utils.invalid_chars_to_fullwidth('a/b\\c:d*e?f"g<h>i|j') == (
        "a／b＼c：d＊e？f＂g＜h＞i｜j"
    )


def test_extrair_essencia_e_titulo_completo():
    assert utils.extrair_essencia("") == ""
    assert utils.extrair_essencia(None) == ""
    assert utils.extrair_essencia("Álbum (Deluxe) [2020]: Vol. 1") == "album vol 1"
    assert utils.extrair_titulo_completo("") == ""
    assert utils.extrair_titulo_completo("Álbum [Deluxe]!") == "album (deluxe)"


class _Resp:
    def __init__(self, dados, status=200):
        self._d, self.status_code = dados, status

    def json(self):
        return self._d


class _Sessao:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.urls = []

    async def get(self, url, **_kw):
        self.urls.append(url)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _res(artista="Banda", album="Disco", faixa=None, capa="http://x/100x100bb.jpg"):
    d = {"artistName": artista, "collectionName": album, "artworkUrl100": capa}
    if faixa:
        d["trackName"] = faixa
    return d


async def test_apple_cover_por_upc():
    s = _Sessao([_Resp({"resultCount": 1, "results": [_res()]})])
    r = await utils.get_apple_hq_cover(s, upc="123", artist="Banda", album="Disco")
    assert r == "http://x/10000x10000bb.jpg"
    assert "lookup?upc=123" in s.urls[0]


async def test_apple_cover_fallback_isrc_e_busca_por_texto():
    s = _Sessao(
        [
            _Resp({"resultCount": 0}),
            RuntimeError("falhou"),
            _Resp({"resultCount": 1, "results": [_res()]}),
        ]
    )
    r = await utils.get_apple_hq_cover(
        s, upc="1", isrc="2", artist="Banda", album="Disco"
    )
    assert r == "http://x/10000x10000bb.jpg"
    assert "search?term=" in s.urls[-1]
    assert "entity=album" in s.urls[-1]


async def test_apple_cover_busca_de_faixa_valida_titulo():
    s = _Sessao([_Resp({"resultCount": 1, "results": [_res(faixa="Musica")]})])
    r = await utils.get_apple_hq_cover(
        s, artist="Banda", album="Disco", track_title="Musica"
    )
    assert r.endswith("10000x10000bb.jpg")
    assert "entity=song" in s.urls[0]


@pytest.mark.parametrize(
    "resultado",
    [
        _res(album="Disco Karaoke"),
        _res(album="Outro Nome Totalmente"),
        _res(artista="Fulano"),
        _res(album="Disco (Deluxe Edition)"),
        {"artistName": "Banda", "artworkUrl100": "x"},
        _res(capa=""),
    ],
)
async def test_apple_cover_rejeita_candidatos_ruins(resultado):
    s = _Sessao([_Resp({"resultCount": 1, "results": [resultado]})])
    assert await utils.get_apple_hq_cover(s, artist="Banda", album="Disco") is None


async def test_apple_cover_rejeita_faixa_diferente():
    s = _Sessao([_Resp({"resultCount": 1, "results": [_res(faixa="Outra Coisa")]})])
    assert (
        await utils.get_apple_hq_cover(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        is None
    )


async def test_apple_cover_sem_dados_e_status_ruim():
    s = _Sessao([_Resp({}, status=500), _Resp({}, status=500)])
    assert (
        await utils.get_apple_hq_cover(s, upc="1", artist="Banda", album="Disco")
        is None
    )
    assert await utils.get_apple_hq_cover(_Sessao([])) is None
    s2 = _Sessao([_Resp({}, 200)])
    assert await utils.get_apple_hq_cover(s2, upc="n/a") is None
    assert s2.urls == []


async def test_apple_cover_cria_cliente_quando_sem_sessao(monkeypatch):
    import httpx

    class Cliente(_Sessao):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    cli = Cliente([_Resp({"resultCount": 1, "results": [_res()]})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: cli)
    r = await utils.get_apple_hq_cover(artist="Banda", album="Disco")
    assert r.endswith("10000x10000bb.jpg")


def test_get_config_paths_variaveis_de_ambiente(monkeypatch, tmp_path):
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    r = utils.get_config_paths()
    assert r["config_dir"] == str(tmp_path)
    assert r["config_file"] == str(tmp_path / "qobuz-dl" / "config.ini")
    assert r["qobuz_db"] == str(tmp_path / "qobuz-dl" / "qobuz_dl.db")


def test_get_config_paths_ios(monkeypatch):
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/ios")
    assert utils.get_config_paths()["config_dir"] == "/ios"
    monkeypatch.delenv("QOBUZ_DL_IOS_HOME")
    monkeypatch.setenv("HOME", "/var/Containers/Data/Application/ABC")
    assert utils.get_config_paths()["config_dir"] == os.path.join(
        "/var/Containers/Data/Application/ABC", "Documents"
    )


def test_get_config_paths_padrao_da_plataforma(monkeypatch):
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    monkeypatch.delenv("QOBUZ_DL_IOS_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setattr(utils.platformdirs, "user_config_dir", lambda: "/cfg")
    assert utils.get_config_paths()["config_dir"] == "/cfg"
