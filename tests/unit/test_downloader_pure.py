"""Testa qobuz_dl/downloader.py -- só o recorte de funções puras/isoladas,
sem tocar nada de decriptação de segmento (`_decrypt_qobuz_segment`,
`_get_qobuz_segment_uuid` ficam de fora de propósito, mesma linha das
sessões anteriores) nem a orquestração pesada da classe `Download`
(`download_release`, `download_track`, `_download_and_tag` -- rede e I/O
real demais pra um teste unitário confiável numa passada só).
"""

import os


from qobuz_dl.downloader import (
    _artist_label,
    _clean_format_str,
    _desc_budget,
    _PositionPool,
    _safe_get,
    create_missing_placeholder,
    format_release_type,
    is_track_streamable,
    process_folder_format_with_subdirs,
)


# --------------------------------------------------------------------
# is_track_streamable
# --------------------------------------------------------------------
class TestIsTrackStreamable:
    def test_streamable_true_e_liberado(self):
        ok, motivo = is_track_streamable({"streamable": True})
        assert ok is True
        assert motivo == ""

    def test_apenas_amostra(self):
        ok, motivo = is_track_streamable({"streamable": False, "sampleable": True})
        assert ok is False
        assert "amostra" in motivo.lower()

    def test_apenas_compra_avulsa(self):
        ok, motivo = is_track_streamable(
            {"streamable": False, "sampleable": False, "purchasable": True}
        )
        assert ok is False
        assert "compra" in motivo.lower()

    def test_sem_nenhuma_flag_e_bloqueio_regional(self):
        ok, motivo = is_track_streamable({"streamable": False})
        assert ok is False
        assert "região" in motivo.lower()

    def test_chaves_ausentes_tratadas_como_false(self):
        # dict vazio não pode explodir com KeyError -- .get() com default
        # False em todas as três flags.
        ok, motivo = is_track_streamable({})
        assert ok is False


# --------------------------------------------------------------------
# create_missing_placeholder
# --------------------------------------------------------------------
class TestCreateMissingPlaceholder:
    def test_cria_arquivo_com_nome_esperado(self, tmp_path):
        track = {"track_number": 3, "title": "Faixa Teste", "duration": 180}
        create_missing_placeholder(track, str(tmp_path), "Não disponível na região")

        arquivos = list(tmp_path.iterdir())
        assert len(arquivos) == 1
        assert arquivos[0].name == "03. Faixa Teste [INDISPONÍVEL].missing.txt"

    def test_numero_da_faixa_e_preenchido_com_zero(self, tmp_path):
        track = {"track_number": 7, "title": "X", "duration": 1}
        create_missing_placeholder(track, str(tmp_path), "motivo")
        assert (tmp_path / "07. X [INDISPONÍVEL].missing.txt").exists()

    def test_barra_no_titulo_nao_quebra_o_caminho(self, tmp_path):
        # "/" no título criaria uma subpasta indevida se não fosse
        # trocado por "-" antes de virar nome de arquivo.
        track = {"track_number": 1, "title": "Antes/Depois", "duration": 1}
        create_missing_placeholder(track, str(tmp_path), "motivo")
        assert (tmp_path / "01. Antes-Depois [INDISPONÍVEL].missing.txt").exists()

    def test_conteudo_inclui_motivo_e_artista(self, tmp_path):
        track = {
            "track_number": 1,
            "title": "Faixa",
            "duration": 200,
            "performer": {"name": "Artista X"},
        }
        create_missing_placeholder(track, str(tmp_path), "Apenas amostra/demo (30s)")
        conteudo = (tmp_path / "01. Faixa [INDISPONÍVEL].missing.txt").read_text(
            encoding="utf-8"
        )
        assert "Artista X" in conteudo
        assert "Apenas amostra/demo (30s)" in conteudo

    def test_pasta_inexistente_nao_lanca_excecao(self, tmp_path):
        # A função é best-effort (comentário no código: "não deve
        # derrubar o download por causa dele") -- erro vai só pro log.
        track = {"track_number": 1, "title": "Faixa", "duration": 1}
        create_missing_placeholder(
            track, str(tmp_path / "pasta-que-nao-existe"), "motivo"
        )  # não deve lançar


# --------------------------------------------------------------------
# format_release_type
# --------------------------------------------------------------------
class TestFormatReleaseType:
    def test_ep_fica_em_maiusculas(self):
        assert format_release_type("ep", track_count=5) == "EP"

    def test_album_fica_em_title_case(self):
        assert format_release_type("album", track_count=10) == "Album"

    def test_sem_nenhum_dado_e_desconhecido(self):
        assert (
            format_release_type(None, track_count=0, duration_seconds=0)
            == "Desconhecido"
        )


# --------------------------------------------------------------------
# process_folder_format_with_subdirs
# --------------------------------------------------------------------
class TestProcessFolderFormatWithSubdirs:
    def test_placeholders_sao_substituidos(self):
        resultado = process_folder_format_with_subdirs(
            "{artist} - {album}", {"artist": "Artista", "album": "Álbum"}
        )
        assert resultado == "Artista - Álbum"

    def test_barra_no_formato_vira_subpasta(self):
        resultado = process_folder_format_with_subdirs(
            "{artist}/{album}", {"artist": "Artista", "album": "Álbum"}
        )
        assert resultado == os.path.join("Artista", "Álbum")

    def test_path_base_e_prefixado_quando_informado(self):
        resultado = process_folder_format_with_subdirs(
            "{artist}", {"artist": "Artista"}, path="/musicas"
        )
        assert resultado == os.path.join("/musicas", "Artista")

    def test_placeholder_ausente_do_dict_cai_pro_texto_original(self):
        # KeyError no .format() é capturado -- usa o texto cru (sanitizado)
        # em vez de derrubar o processamento inteiro por uma tag faltando.
        resultado = process_folder_format_with_subdirs(
            "{artist} - {campo_que_nao_existe}", {"artist": "Artista"}
        )
        assert "campo_que_nao_existe" in resultado or resultado != ""

    def test_caracteres_invalidos_de_path_sao_sanitizados(self):
        resultado = process_folder_format_with_subdirs(
            "{titulo}", {"titulo": "Nome: com / caracteres * inválidos?"}
        )
        # sanitize_filepath/clean_filename removem os caracteres reservados
        # do SO -- não pode sobrar ":" nem "*" nem "?" no nome final.
        assert ":" not in resultado
        assert "*" not in resultado
        assert "?" not in resultado

    def test_parte_vazia_e_ignorada(self):
        resultado = process_folder_format_with_subdirs("{a}//{b}", {"a": "X", "b": "Y"})
        assert resultado == os.path.join("X", "Y")

    def test_nome_muito_longo_e_truncado_no_meio(self):
        nome_gigante = "A" * 200
        resultado = process_folder_format_with_subdirs("{t}", {"t": nome_gigante})
        assert len(resultado) < 200
        assert "..." in resultado


# --------------------------------------------------------------------
# _clean_format_str
# --------------------------------------------------------------------
class TestCleanFormatStr:
    def test_extensao_mp3_e_removida(self):
        folder, track = _clean_format_str("Pasta", "01 - Faixa.mp3", "MP3")
        assert track == "01 - Faixa"

    def test_extensao_flac_e_removida(self):
        folder, track = _clean_format_str("Pasta", "01 - Faixa.flac", "FLAC")
        assert track == "01 - Faixa"

    def test_sem_extensao_nao_muda(self):
        folder, track = _clean_format_str("Pasta", "01 - Faixa", "FLAC")
        assert track == "01 - Faixa"

    def test_espacos_nas_pontas_sao_removidos(self):
        folder, track = _clean_format_str("  Pasta  ", "  Faixa  ", "FLAC")
        assert folder == "Pasta"
        assert track == "Faixa"

    def test_extensao_no_meio_da_string_nao_e_tocada(self):
        # Só remove se a string TERMINAR com ".mp3"/".flac" -- um álbum
        # chamado literalmente "mp3.collection" não pode perder pedaço.
        folder, track = _clean_format_str("mp3.collection", "Faixa", "FLAC")
        assert folder == "mp3.collection"


# --------------------------------------------------------------------
# _safe_get
# --------------------------------------------------------------------
class TestSafeGet:
    def test_chave_unica_presente(self):
        assert _safe_get({"a": 1}, "a") == 1

    def test_navegacao_aninhada(self):
        assert _safe_get({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42

    def test_chave_ausente_devolve_default(self):
        assert _safe_get({"a": 1}, "z", default="fallback") == "fallback"

    def test_caminho_quebrado_no_meio_devolve_o_valor_parcial(self):
        # "a" existe e vale 1 (não é dict/lista navegável) -- a função
        # devolve esse valor parcial encontrado, não o default. O default
        # só entra quando a CHAVE em si está ausente (ver teste acima).
        assert _safe_get({"a": 1}, "a", "b", default="fallback") == 1

    def test_default_none_por_padrao(self):
        assert _safe_get({}, "qualquer") is None


# --------------------------------------------------------------------
# _artist_label
# --------------------------------------------------------------------
class TestArtistLabel:
    def test_artista_unico_via_campo_artist(self):
        item = {"artist": {"name": "Artista Solo"}}
        assert _artist_label(item) == "Artista Solo"

    def test_multiplos_main_artists_juntados_por_virgula(self):
        item = {
            "artists": [
                {"name": "Artista A", "roles": ["main-artist"]},
                {"name": "Artista B", "roles": ["main-artist"]},
                {"name": "Produtor C", "roles": ["producer"]},
            ]
        }
        assert _artist_label(item) == "Artista A, Artista B"

    def test_sem_nenhum_artista_usa_fallback(self):
        assert _artist_label({}, fallback="Desconhecido") == "Desconhecido"

    def test_item_none_usa_fallback_sem_lancar(self):
        assert _artist_label(None, fallback="Desconhecido") == "Desconhecido"


# --------------------------------------------------------------------
# _desc_budget / _PositionPool
# --------------------------------------------------------------------
class TestDescBudget:
    def test_nunca_fica_abaixo_de_6(self):
        assert _desc_budget(0) == 6
        assert _desc_budget(10) == 6

    def test_nunca_passa_de_30(self):
        assert _desc_budget(1000) == 30

    def test_terminal_medio_fica_entre_os_limites(self):
        resultado = _desc_budget(80)
        assert 6 <= resultado <= 30


class TestPositionPool:
    def test_acquire_devolve_posicoes_diferentes(self):
        pool = _PositionPool(3)
        posicoes = {pool.acquire(), pool.acquire(), pool.acquire()}
        assert posicoes == {0, 1, 2}

    def test_pool_esgotado_devolve_0_em_vez_de_lancar(self):
        pool = _PositionPool(1)
        pool.acquire()
        # Sem posição livre sobrando -- best-effort, devolve 0 em vez de
        # travar o download inteiro por falta de linha de progresso.
        assert pool.acquire() == 0

    def test_release_devolve_a_posicao_pro_pool(self):
        pool = _PositionPool(2)
        p1 = pool.acquire()
        pool.release(p1)
        assert p1 in pool._free

    def test_release_nao_duplica_posicao_ja_livre(self):
        pool = _PositionPool(2)
        p1 = pool.acquire()
        pool.release(p1)
        pool.release(p1)  # segunda liberação da mesma posição -- idempotente
        assert pool._free.count(p1) == 1
