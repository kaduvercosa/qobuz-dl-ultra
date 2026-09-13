"""Testa as funções de qobuz_dl/utils.py que não dependem de rede nem de
binário externo de verdade -- este arquivo não tinha NENHUM teste
dedicado antes, apesar de conter `classify_release_type` (usada em toda
gravação de tag e nomeação de pasta) e `get_album_artist`.

Fora de escopo aqui, de propósito: `make_m3u` (I/O pesado, varre pasta e
lê tags reais), `smart_discography_filter`, `clean_filename` /
`apply_legacy_charmap` / `invalid_chars_to_fullwidth` (fila própria,
ainda não vista), e `extrair_essencia`/`extrair_titulo_completo` (são
closures aninhadas dentro da função de busca de capa via iTunes, mesmo
problema estrutural do `process_track` antes da extração).
"""

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from qobuz_dl import utils

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------
# classify_release_type
# --------------------------------------------------------------------
class TestClassifyReleaseType:
    def test_live_via_versao(self):
        assert utils.classify_release_type(version="Live") == "live"

    def test_live_via_titulo_com_parenteses(self):
        assert utils.classify_release_type(title="Album (Live)") == "live"

    def test_live_via_titulo_com_travessao(self):
        assert utils.classify_release_type(title="Show - Live") == "live"

    @pytest.mark.parametrize(
        "kw", ["Best Of", "Greatest Hits", "Anthology", "Collection", "Compilation"]
    )
    def test_compilation_por_palavra_chave(self, kw):
        assert utils.classify_release_type(title=f"The {kw}") == "compilation"

    def test_compilation_tem_prioridade_sobre_contagem_de_faixas(self):
        # 20 faixas seria "album" pela contagem, mas a palavra-chave no
        # título vence -- é sinal de intenção humana explícita.
        assert (
            utils.classify_release_type(title="Greatest Hits", track_count=20)
            == "compilation"
        )

    def test_ep_via_titulo_ou_versao(self):
        assert utils.classify_release_type(title="Faixas Extras EP") == "ep"
        assert utils.classify_release_type(version="EP") == "ep"

    @pytest.mark.parametrize(
        "faixas,esperado",
        [
            (1, "single"),
            (3, "single"),
            (4, "ep"),
            (7, "ep"),
            (8, "album"),
            (20, "album"),
        ],
    )
    def test_contagem_de_faixas_bate_com_a_regra_oficial(self, faixas, esperado):
        assert utils.classify_release_type(track_count=faixas) == esperado

    def test_contagem_de_faixas_vence_mesmo_com_api_dizendo_outra_coisa(self):
        # 5 faixas = EP pela regra, mesmo a API tendo mandado "single".
        assert (
            utils.classify_release_type(track_count=5, api_release_type="single")
            == "ep"
        )

    def test_sem_contagem_cai_pra_duracao_longa_como_album(self):
        assert utils.classify_release_type(duration_seconds=1740) == "album"
        assert utils.classify_release_type(duration_seconds=1739) != "album"

    def test_sem_contagem_nem_duracao_longa_cai_pra_tag_da_api(self):
        assert (
            utils.classify_release_type(duration_seconds=100, api_release_type="ep")
            == "ep"
        )

    def test_sem_nenhum_sinal_cai_pro_item_type(self):
        assert utils.classify_release_type(item_type="download") == "download"

    def test_titulo_e_versao_none_nao_lancam_excecao(self):
        # Uso real (ex.: album.get("title")) pode devolver None -- não
        # pode quebrar com AttributeError em .lower().
        assert utils.classify_release_type(title=None, version=None) == "unknown"


# --------------------------------------------------------------------
# get_album_artist
# --------------------------------------------------------------------
class TestGetAlbumArtist:
    def test_sem_chave_artists_cai_pro_artist_unico(self):
        album = {"artist": {"name": "Artista Solo"}}
        assert utils.get_album_artist(album) == ["Artista Solo"]

    def test_filtra_so_quem_tem_role_main_artist(self):
        album = {
            "artists": [
                {"name": "Artista A", "roles": ["main-artist"]},
                {"name": "Produtor X", "roles": ["producer"]},
                {"name": "Artista B", "roles": ["main-artist", "composer"]},
            ]
        }
        assert utils.get_album_artist(album) == ["Artista A", "Artista B"]

    def test_artists_presente_sem_nenhum_main_artist_cai_pro_artist_unico(self):
        album = {
            "artists": [{"name": "Produtor X", "roles": ["producer"]}],
            "artist": {"name": "Fallback"},
        }
        assert utils.get_album_artist(album) == ["Fallback"]

    def test_album_vazio_devolve_lista_vazia(self):
        assert utils.get_album_artist({}) == []

    def test_artist_sem_name_devolve_lista_vazia(self):
        assert utils.get_album_artist({"artist": {}}) == []


# --------------------------------------------------------------------
# get_url_info
# --------------------------------------------------------------------
class TestGetUrlInfo:
    def test_url_completa_www(self):
        tipo, item_id = utils.get_url_info(
            "https://www.qobuz.com/us-en/album/nome-do-album/12345"
        )
        assert tipo == "album"
        assert item_id == "12345"

    def test_url_open_qobuz(self):
        tipo, item_id = utils.get_url_info("https://open.qobuz.com/track/98765")
        assert tipo == "track"
        assert item_id == "98765"

    def test_url_play_qobuz(self):
        tipo, item_id = utils.get_url_info("https://play.qobuz.com/playlist/555")
        assert tipo == "playlist"
        assert item_id == "555"

    def test_caminho_relativo_sem_dominio(self):
        tipo, item_id = utils.get_url_info("/us-en/artist/-/4242")
        assert tipo == "artist"
        assert item_id == "4242"

    def test_label(self):
        tipo, item_id = utils.get_url_info(
            "https://www.qobuz.com/us-en/label/nome-da-gravadora/777"
        )
        assert tipo == "label"
        assert item_id == "777"

    def test_url_sem_match_lanca_attribute_error(self):
        # r.groups() num re.search que não casou (None) -- comportamento
        # atual: quem chama precisa validar a URL antes, não há fallback
        # gracioso aqui.
        with pytest.raises(AttributeError):
            utils.get_url_info("https://exemplo.com/nao-e-uma-url-qobuz")


# --------------------------------------------------------------------
# format_duration
# --------------------------------------------------------------------
class TestFormatDuration:
    def test_menos_de_um_minuto(self):
        assert utils.format_duration(45) == "00:00:45"

    def test_minutos_e_segundos(self):
        assert utils.format_duration(125) == "00:02:05"

    def test_uma_hora_exata(self):
        assert utils.format_duration(3600) == "01:00:00"

    def test_zero(self):
        assert utils.format_duration(0) == "00:00:00"


# --------------------------------------------------------------------
# PartialFormatter
# --------------------------------------------------------------------
class TestPartialFormatter:
    def test_campo_presente_formata_normalmente(self):
        fmt = utils.PartialFormatter()
        assert fmt.format("{artist} - {title}", artist="X", title="Y") == "X - Y"

    def test_campo_ausente_vira_missing(self):
        fmt = utils.PartialFormatter()
        assert fmt.format("{artist} - {title}", artist="X") == "X - n/a"

    def test_campo_vazio_tambem_vira_missing(self):
        fmt = utils.PartialFormatter()
        assert fmt.format("{artist}", artist="") == "n/a"

    def test_missing_customizado(self):
        fmt = utils.PartialFormatter(missing="desconhecido")
        assert fmt.format("{artist}") == "desconhecido"

    def test_spec_invalido_para_string_vira_bad_fmt(self):
        fmt = utils.PartialFormatter()
        # ":03d" é spec numérico; aplicado numa string, ValueError vira
        # bad_fmt em vez de propagar.
        assert fmt.format("{titulo:03d}", titulo="abc") == "n/a"


# --------------------------------------------------------------------
# create_and_return_dir
# --------------------------------------------------------------------
class TestCreateAndReturnDir:
    def test_cria_diretorio_que_nao_existe(self, tmp_path):
        alvo = tmp_path / "nova" / "pasta"
        resultado = utils.create_and_return_dir(str(alvo))

        assert os.path.isdir(resultado)
        assert resultado == os.path.abspath(str(alvo))

    def test_diretorio_ja_existente_nao_lanca_excecao(self, tmp_path):
        resultado = utils.create_and_return_dir(str(tmp_path))
        assert resultado == os.path.abspath(str(tmp_path))


# --------------------------------------------------------------------
# get_config_paths
# --------------------------------------------------------------------
class TestGetConfigPaths:
    def test_config_dir_explicito_tem_prioridade_maxima(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/deveria/ser/ignorado")

        caminhos = utils.get_config_paths()

        assert caminhos["config_dir"] == str(tmp_path)
        assert caminhos["config_path"] == os.path.join(str(tmp_path), "qobuz-dl")
        assert caminhos["config_file"].endswith("config.ini")
        assert caminhos["qobuz_db"].endswith("qobuz_dl.db")

    def test_ios_home_usado_quando_config_dir_ausente(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CONFIG_DIR", raising=False)
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", str(tmp_path))

        caminhos = utils.get_config_paths()

        assert caminhos["config_dir"] == str(tmp_path)

    def test_deteccao_automatica_de_ios_via_home(self, monkeypatch):
        monkeypatch.delenv("CONFIG_DIR", raising=False)
        monkeypatch.delenv("QOBUZ_DL_IOS_HOME", raising=False)
        monkeypatch.setenv("HOME", "/private/var/.../Containers/Data/Application/ABC")

        caminhos = utils.get_config_paths()

        assert caminhos["config_dir"].endswith("Documents")


# --------------------------------------------------------------------
# encontrar_binario
# --------------------------------------------------------------------
class TestEncontrarBinario:
    @pytest.fixture(autouse=True)
    def _cache_limpo(self):
        # _BINARIOS_CHECADOS é um dict global -- sem isolar, um teste
        # contamina o cache dos outros (e da suíte inteira, já que o
        # processo continua rodando depois deste arquivo).
        original = dict(utils._BINARIOS_CHECADOS)
        utils._BINARIOS_CHECADOS.clear()
        yield
        utils._BINARIOS_CHECADOS.clear()
        utils._BINARIOS_CHECADOS.update(original)

    def test_encontrado_no_path_e_cacheado(self, monkeypatch):
        chamadas = []

        def fake_which(nome, path=None):
            chamadas.append((nome, path))
            return "/usr/bin/ffmpeg" if path is None else None

        monkeypatch.setattr(shutil, "which", fake_which)

        assert utils.encontrar_binario("ffmpeg") == "/usr/bin/ffmpeg"
        assert utils.encontrar_binario("ffmpeg") == "/usr/bin/ffmpeg"
        # segunda chamada veio do cache -- shutil.which só roda uma vez
        assert len(chamadas) == 1

    def test_nao_encontrado_em_lugar_nenhum_devolve_none(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda nome, path=None: None)

        assert utils.encontrar_binario("binario-que-nao-existe") is None


# --------------------------------------------------------------------
# checar_binarios_externos
# --------------------------------------------------------------------
class TestCheckarBinariosExternos:
    @pytest.fixture
    def ui_spy(self, monkeypatch):
        chamadas = []

        def _fabrica(nome):
            def _fn(*args, **kwargs):
                chamadas.append((nome, args))

            return _fn

        from qobuz_dl import ui

        monkeypatch.setattr(ui, "warn", _fabrica("warn"))
        monkeypatch.setattr(ui, "wrapped", _fabrica("wrapped"))
        return chamadas

    def test_ffmpeg_encontrado_sem_fpcalc_nao_avisa_nada(self, monkeypatch, ui_spy):
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")

        resultado = utils.checar_binarios_externos(precisa_fpcalc=False)

        assert resultado == {"ffmpeg": "/usr/bin/ffmpeg", "fpcalc": None}
        assert ui_spy == []

    def test_ffmpeg_ausente_avisa(self, monkeypatch, ui_spy):
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: None)

        resultado = utils.checar_binarios_externos()

        assert resultado["ffmpeg"] is None
        assert any(
            nome == "warn" and "ffmpeg nao encontrado" in args[0]
            for nome, args in ui_spy
        )

    def test_fpcalc_nao_e_checado_quando_nao_precisa(self, monkeypatch, ui_spy):
        chamados = []
        monkeypatch.setattr(
            utils,
            "encontrar_binario",
            lambda nome: chamados.append(nome) or "/usr/bin/ffmpeg",
        )

        utils.checar_binarios_externos(precisa_fpcalc=False)

        assert chamados == ["ffmpeg"]  # fpcalc nunca foi checado

    def test_fpcalc_ausente_quando_precisa_avisa(self, monkeypatch, ui_spy):
        monkeypatch.setattr(
            utils,
            "encontrar_binario",
            lambda nome: "/usr/bin/ffmpeg" if nome == "ffmpeg" else None,
        )

        resultado = utils.checar_binarios_externos(precisa_fpcalc=True)

        assert resultado["fpcalc"] is None
        assert any(
            nome == "warn" and "fpcalc nao encontrado" in args[0]
            for nome, args in ui_spy
        )

    def test_fpcalc_encontrado_quando_precisa_nao_avisa_fpcalc(
        self, monkeypatch, ui_spy
    ):
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: f"/usr/bin/{nome}")

        resultado = utils.checar_binarios_externos(precisa_fpcalc=True)

        assert resultado == {"ffmpeg": "/usr/bin/ffmpeg", "fpcalc": "/usr/bin/fpcalc"}
        assert not any("fpcalc" in args[0] for _, args in ui_spy)


# --------------------------------------------------------------------
# verify_audio_integrity
# --------------------------------------------------------------------
class TestVerifyAudioIntegrity:
    def test_arquivo_inexistente(self, tmp_path):
        ok, motivo = utils.verify_audio_integrity(str(tmp_path / "nao-existe.flac"))
        assert ok is False
        assert "nao encontrado" in motivo.lower()

    def test_ffmpeg_nao_disponivel(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: None)

        ok, motivo = utils.verify_audio_integrity(str(arquivo))

        assert ok is False
        assert "ffmpeg nao disponivel" in motivo.lower()

    def test_sucesso_returncode_zero_sem_stderr(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            utils.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stderr=""),
        )

        ok, motivo = utils.verify_audio_integrity(str(arquivo))

        assert (ok, motivo) == (True, "")

    def test_returncode_diferente_de_zero_e_falha(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            utils.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=1, stderr=""),
        )

        ok, motivo = utils.verify_audio_integrity(str(arquivo))

        assert ok is False
        assert "codigo 1" in motivo

    def test_stderr_com_conteudo_e_falha_mesmo_com_returncode_zero(
        self, monkeypatch, tmp_path
    ):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            utils.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=0, stderr="Invalid data found\n"
            ),
        )

        ok, motivo = utils.verify_audio_integrity(str(arquivo))

        assert ok is False
        assert "Invalid data found" in motivo

    def test_ffmpeg_desaparece_entre_o_encontrar_e_o_rodar(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")

        def _explode(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(utils.subprocess, "run", _explode)

        ok, motivo = utils.verify_audio_integrity(str(arquivo))

        assert ok is False
        assert "ffmpeg nao encontrado no sistema" in motivo.lower()

    def test_timeout(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x")
        monkeypatch.setattr(utils, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg")

        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5)

        monkeypatch.setattr(utils.subprocess, "run", _timeout)

        ok, motivo = utils.verify_audio_integrity(str(arquivo), timeout=5)

        assert ok is False
        assert "5s" in motivo


# --------------------------------------------------------------------
# smart_discography_filter
# --------------------------------------------------------------------
def _album_item(
    titulo, artista="Artista X", bit_depth=24, sampling_rate=96.0, versao="", item_id=1
):
    return {
        "id": item_id,
        "title": titulo,
        "version": versao,
        "maximum_bit_depth": bit_depth,
        "maximum_sampling_rate": sampling_rate,
        "artist": {"name": artista},
    }


def _contents(albums, requested_artist="Artista X"):
    return [{"name": requested_artist, "albums": {"items": albums}}]


class TestSmartDiscographyFilter:
    def test_remove_album_onde_artista_pedido_e_so_feature(self):
        albums = [
            _album_item("Album A", artista="Artista X", item_id=1),
            _album_item("Album A", artista="Outro Artista", item_id=2),
        ]
        resultado = utils.smart_discography_filter(_contents(albums))
        assert [a["id"] for a in resultado] == [1]

    def test_mantem_a_melhor_qualidade_entre_duplicatas(self):
        albums = [
            _album_item("Album B", bit_depth=16, sampling_rate=44.1, item_id=1),
            _album_item("Album B", bit_depth=24, sampling_rate=96.0, item_id=2),
            _album_item("Album B", bit_depth=24, sampling_rate=192.0, item_id=3),
        ]
        resultado = utils.smart_discography_filter(_contents(albums))
        assert [a["id"] for a in resultado] == [3]

    def test_save_space_escolhe_a_menor_taxa_de_amostragem(self):
        albums = [
            _album_item("Album C", sampling_rate=96.0, item_id=1),
            _album_item("Album C", sampling_rate=192.0, item_id=2),
        ]
        resultado = utils.smart_discography_filter(_contents(albums), save_space=True)
        assert [a["id"] for a in resultado] == [1]

    def test_quando_existe_remaster_no_grupo_so_ele_sobrevive(self):
        albums = [
            _album_item("Album D", versao="", item_id=1),
            _album_item("Album D", versao="Remastered", item_id=2),
        ]
        resultado = utils.smart_discography_filter(_contents(albums))
        assert [a["id"] for a in resultado] == [2]

    def test_skip_extras_remove_deluxe(self):
        albums = [
            _album_item("Album E", versao="", item_id=1),
            _album_item("Album E", versao="Deluxe Edition", item_id=2),
        ]
        resultado = utils.smart_discography_filter(_contents(albums), skip_extras=True)
        assert [a["id"] for a in resultado] == [1]

    def test_grupos_de_essencia_diferentes_sobrevivem_independentemente(self):
        albums = [_album_item("Album F", item_id=1), _album_item("Album G", item_id=2)]
        resultado = utils.smart_discography_filter(_contents(albums))
        assert {a["id"] for a in resultado} == {1, 2}


# --------------------------------------------------------------------
# invalid_chars_to_fullwidth / apply_legacy_charmap / clean_filename
# --------------------------------------------------------------------
class TestInvalidCharsToFullwidth:
    def test_cada_caractere_invalido_vira_seu_equivalente_fullwidth(self):
        assert utils.invalid_chars_to_fullwidth("A/B") == "A／B"
        assert utils.invalid_chars_to_fullwidth("A:B") == "A：B"
        assert utils.invalid_chars_to_fullwidth('A"B') == "A＂B"

    def test_sem_caractere_invalido_nao_muda(self):
        assert utils.invalid_chars_to_fullwidth("Nome Normal") == "Nome Normal"


class TestApplyLegacyCharmap:
    def test_dois_pontos_vira_hifen_e_interrogacao_e_removida(self):
        assert utils.apply_legacy_charmap("A: Pergunta?") == "A- Pergunta"

    def test_menor_e_maior_viram_colchetes(self):
        assert utils.apply_legacy_charmap("A<B>C") == "A[B]C"

    def test_hifens_duplos_gerados_pela_substituicao_colapsam(self):
        # ":" e "/" adjacentes viram "--" depois das duas substituições
        # separadas -- o colapso de hífen duplo entra em ação aqui.
        assert utils.apply_legacy_charmap("A:/B") == "A -B"


class TestCleanFilename:
    def test_normaliza_unicode_para_nfc(self):
        decomposto = "e\u0301"  # "e" + acento agudo combinante
        precomposto = "é"
        assert utils.clean_filename(decomposto) == utils.clean_filename(precomposto)

    def test_separadores_repetidos_colapsam_em_um_so(self):
        assert utils.clean_filename("A,,B") == "A, B"

    def test_parenteses_vazios_sao_removidos(self):
        assert utils.clean_filename("Album ()") == "Album"

    def test_parenteses_com_texto_util_sao_preservados(self):
        assert utils.clean_filename("Album (Deluxe)") == "Album (Deluxe)"

    def test_espacos_multiplos_colapsam(self):
        assert utils.clean_filename("A    B") == "A B"

    def test_pontos_finais_sao_removidos(self):
        assert utils.clean_filename("Nome do Album...") == "Nome do Album"

    def test_legacy_charmap_true_usa_ascii(self):
        assert utils.clean_filename("A/B", legacy_charmap=True) == "A-B"

    def test_legacy_charmap_false_usa_fullwidth_por_padrao(self):
        assert utils.clean_filename("A/B", legacy_charmap=False) == "A／B"
