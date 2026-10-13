"""Testa os helpers de `qobuz_dl/retro_tags.py` (comando `tags`).

`test_retro_tags.py` cobre o laço principal (`retag_directory`). Aqui ficam as
peças menores, sem rede e sem arquivo de áudio real:

* resolução da pasta alvo (sandbox do iOS/a-Shell);
* cache de álbuns e resolução de metadados faixa -> álbum;
* montagem do pedido à Apple (título + versão, artista, UPC/ISRC);
* download temporário da capa e limpeza do arquivo parcial.
"""

import os

import pytest

from qobuz_dl import retro_tags

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# resolve_library_dir
# ---------------------------------------------------------------------------
class TestResolveLibraryDir:
    def test_expande_til(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/fulano")
        assert retro_tags.resolve_library_dir("~/Musica") == os.path.join(
            "/home/fulano", "Musica"
        )

    def test_caminho_normal_nao_muda(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", "/home/fulano")
        alvo = str(tmp_path / "FLAC")
        assert retro_tags.resolve_library_dir(alvo) == alvo

    def test_ios_move_pasta_de_fora_para_documents(self, monkeypatch):
        home = "/private/var/mobile/Containers/Data/Application/ABC"
        monkeypatch.setenv("HOME", home)
        assert retro_tags.resolve_library_dir("/outro/lugar/FLAC") == os.path.join(
            home, "Documents", "FLAC"
        )

    def test_ios_barra_final_nao_atrapalha(self, monkeypatch):
        home = "/private/var/mobile/Containers/Data/Application/ABC"
        monkeypatch.setenv("HOME", home)
        assert retro_tags.resolve_library_dir("/outro/FLAC/") == os.path.join(
            home, "Documents", "FLAC"
        )

    def test_ios_pasta_ja_em_documents_fica(self, monkeypatch):
        home = "/private/var/mobile/Containers/Data/Application/ABC"
        monkeypatch.setenv("HOME", home)
        alvo = os.path.join(home, "Documents", "FLAC")
        assert retro_tags.resolve_library_dir(alvo) == alvo

    def test_ios_raiz_sem_nome_usa_pasta_padrao(self, monkeypatch):
        home = "/private/var/mobile/Containers/Data/Application/ABC"
        monkeypatch.setenv("HOME", home)
        assert retro_tags.resolve_library_dir("/") == os.path.join(
            home, "Documents", "Qobuz Downloads"
        )


def test_multi_tags_label():
    class S:
        multi_value_tags = True

    class N:
        multi_value_tags = False

    assert retro_tags.multi_tags_label(S()) == "ATIVADO"
    assert retro_tags.multi_tags_label(N()) == "DESATIVADO"
    assert retro_tags.multi_tags_label(object()) == "DESATIVADO"


# ---------------------------------------------------------------------------
# Formatação da saída (cores com significado fixo, blocos espaçados)
# ---------------------------------------------------------------------------
class TestFileState:
    @pytest.mark.parametrize(
        "tags, capa, atencao, cor, texto",
        [
            (True, True, False, "G", "Alterado -- tags e capa"),
            (True, False, False, "G", "Alterado -- somente as tags"),
            (False, True, False, "G", "Alterado -- somente a capa"),
            (True, False, True, "Y", "Alterado (somente as tags), mas ficou uma pendência na capa"),
            (True, True, True, "Y", "Alterado (tags e capa), mas ficou uma pendência na capa"),
            (False, False, False, "M", "Sem alterações"),
            (False, False, True, "M", "Sem alterações"),  # a capa é que ganha o amarelo
        ],
    )
    def test_cor_e_texto(self, monkeypatch, tags, capa, atencao, cor, texto):
        for nome, marca in (("GREEN", "G"), ("YELLOW", "Y"), ("MUTED", "M")):
            monkeypatch.setattr(retro_tags, nome, marca)
        assert retro_tags._file_state(tags, capa, atencao) == (cor, texto)


class TestCoverLine:
    @pytest.mark.parametrize(
        "desfecho, mudou, cor",
        [
            ("replaced", True, "G"),
            ("replaced", False, "M"),
            ("kept", False, "M"),
            ("not_found", False, "Y"),
            ("download_failed", False, "Y"),
            ("embed_failed", False, "Y"),
            ("error", False, "Y"),
            ("not_searched", False, "M"),
        ],
    )
    def test_cor_por_desfecho(self, monkeypatch, desfecho, mudou, cor):
        for nome, marca in (("GREEN", "G"), ("YELLOW", "Y"), ("MUTED", "M")):
            monkeypatch.setattr(retro_tags, nome, marca)
        assert retro_tags._cover_line(desfecho, mudou, "Apple/iTunes")[0] == cor

    def test_textos(self):
        linha = retro_tags._cover_line
        assert linha("replaced", True, "Apple/iTunes")[1] == (
            "substituída pela capa da Apple/iTunes"
        )
        assert linha("replaced", True, None)[1].endswith("Apple/iTunes")
        assert "igual ou maior" in linha("kept", False, None)[1]
        assert "capa atual foi mantida" in linha("not_found", False, None)[1]

    def test_desfechos_de_atencao(self):
        assert retro_tags._COVER_ATTENTION == {
            "not_found",
            "download_failed",
            "embed_failed",
            "error",
        }


class TestSplitAppleDiagnostic:
    def test_separa_motivo_etapa_do_album_e_exemplos(self):
        texto = (
            "versão incompatível (candidato mais próximo; 1 rejeitado(s) no total)"
            "; etapa do álbum: br: nenhum álbum compatível (1 vistos)"
            "; erros de rede: HTTP 503"
            "; exemplos: motivo A: x | motivo B: y"
        )
        assert retro_tags.split_apple_diagnostic(texto) == [
            (
                "Motivo",
                "versão incompatível (candidato mais próximo; 1 rejeitado(s) no total)",
            ),
            ("Etapa do álbum", "br: nenhum álbum compatível (1 vistos)"),
            ("Erros de rede", "HTTP 503"),
            ("Exemplos", "motivo A: x | motivo B: y"),
        ]

    def test_so_motivo(self):
        assert retro_tags.split_apple_diagnostic("artista incompatível") == [
            ("Motivo", "artista incompatível")
        ]

    @pytest.mark.parametrize("vazio", ["", None])
    def test_vazio(self, vazio):
        assert retro_tags.split_apple_diagnostic(vazio) == []

    def test_nao_corta_ponto_e_virgula_dentro_de_uma_nota(self):
        texto = "m; etapa do álbum: br: a; us: b; exemplos: e"
        secoes = dict(retro_tags.split_apple_diagnostic(texto))
        assert secoes["Etapa do álbum"] == "br: a; us: b"


class TestWrapEDetalhes:
    def test_wrap_respeita_largura_e_recuo(self):
        linhas = retro_tags._wrap_lines("palavra " * 30, 10, width=40)
        assert len(linhas) > 1
        assert all(len(ln) <= 39 and ln.startswith(" " * 10) for ln in linhas)

    def test_palavra_gigante_nao_estoura(self):
        linhas = retro_tags._wrap_lines("x" * 200, 4, width=40)
        assert all(len(ln) <= 39 for ln in linhas)

    def test_labeled_alinha_a_continuacao_sob_o_texto(self):
        linhas = retro_tags._labeled_lines("Capa", "palavra " * 20, 40)
        assert linhas[0].startswith("   Capa   palavra")
        assert all(ln.startswith(" " * 10) for ln in linhas[1:])

    def test_detalhe_do_nao_encontrado_separa_secoes_e_exemplos(self):
        texto = "motivo; etapa do álbum: br: ok; exemplos: ex1 | ex2"
        linhas = retro_tags._cover_detail_lines("not_found", None, texto, 100)
        planas = [ln.strip() for ln in linhas]
        assert "Motivo: motivo" in planas
        assert "Etapa do álbum: br: ok" in planas
        assert "Exemplos:" in planas
        assert "• ex1" in planas and "• ex2" in planas

    def test_detalhe_da_capa_substituida(self):
        linhas = retro_tags._cover_detail_lines(
            "replaced", "Apple/iTunes", "capa baixada (3 KiB)", 100
        )
        assert [ln.strip() for ln in linhas] == [
            "Fonte: Apple/iTunes | capa baixada (3 KiB)"
        ]

    def test_kept_nao_tem_detalhe(self):
        assert retro_tags._cover_detail_lines("kept", None, "x", 100) == []

    def test_term_width_fica_entre_60_e_120(self, monkeypatch):
        for colunas, esperado in (("20", 60), ("90", 90), ("300", 120)):
            monkeypatch.setenv("COLUMNS", colunas)
            assert retro_tags._term_width() == esperado


class TestContagemDeTags:
    def test_conta_chaves_diferentes(self):
        antes = {"A": ["1"], "B": ["2"], "C": ["3"]}
        depois = {"A": ["1"], "B": ["9"], "D": ["4"]}
        assert retro_tags.count_changed_tags(antes, depois) == 3  # B, C, D

    def test_iguais_e_entradas_invalidas(self):
        assert retro_tags.count_changed_tags({"A": ["1"]}, {"A": ["1"]}) == 0
        assert retro_tags.count_changed_tags(None, "x") == 0


class TestCabecalhoDoAlbum:
    def _album(self, **extra):
        album = {"title": "Anti-Herói", "artist": {"name": "Jão"}, "tracks_count": 10}
        album.update(extra)
        return album

    def test_titulo_artista_tipo_e_faixas(self):
        linhas = retro_tags._album_header_lines(self._album(), None, 80)
        assert linhas[0].startswith("== Anti-Herói -- Jão  |  álbum | 10 faixas ")
        assert linhas[0].endswith("=") and len(linhas[0]) <= 80
        assert len(linhas) == 1  # sem decisão informada: só o cabeçalho

    def test_com_decisao_mostra_o_metodo(self):
        decisao = {"url": "u", "method": "consenso de 8/9 faixas"}
        linhas = retro_tags._album_header_lines(self._album(), decisao, 80)
        assert linhas[1] == (
            "   Capa Apple única para todas as faixas (consenso de 8/9 faixas)"
        )

    def test_sem_url_explica_que_cada_faixa_e_avaliada_sozinha(self):
        linhas = retro_tags._album_header_lines(self._album(), {"url": None}, 80)
        assert "cada faixa é avaliada separadamente" in linhas[1]

    def test_sem_titulo_nao_tem_cabecalho(self):
        assert retro_tags._album_header_lines({"id": "X"}, None, 80) == []

    def test_titulo_longo_e_truncado_sem_estourar_a_largura(self):
        linhas = retro_tags._album_header_lines(
            self._album(title="T" * 200), None, 60
        )
        assert len(linhas[0]) <= 60

    def test_singular_para_uma_faixa(self):
        linhas = retro_tags._album_header_lines(self._album(tracks_count=1), None, 80)
        assert "single | 1 faixa " in linhas[0]


def test_legenda_explica_as_quatro_cores():
    texto = "\n".join(retro_tags._legend_lines())
    for palavra in ("verde", "amarelo", "cinza", "vermelho"):
        assert palavra in texto


def test_muted_nao_e_mais_o_mesmo_que_amarelo():
    # MUTED era um apelido de WARNING (amarelo): "sem alteração" e "capa
    # substituída" saíam da mesma cor. Agora vem do GRAY do color.py.
    from qobuz_dl import color

    assert retro_tags.MUTED is color.GRAY
    assert color.GRAY == "" or color.GRAY != color.WARNING


# ---------------------------------------------------------------------------
# _fetch_album / _resolve_metadata
# ---------------------------------------------------------------------------
class _Cliente:
    def __init__(self, albuns=None, faixas=None):
        self.albuns = albuns or {}
        self.faixas = faixas or {}
        self.album_calls = []
        self.track_calls = []

    async def get_album_meta(self, album_id):
        self.album_calls.append(album_id)
        if album_id not in self.albuns:
            raise RuntimeError("álbum inexistente")
        return self.albuns[album_id]

    async def get_track_meta(self, track_id):
        self.track_calls.append(track_id)
        if track_id not in self.faixas:
            raise RuntimeError("faixa inexistente")
        return self.faixas[track_id]


class TestFetchAlbum:
    async def test_busca_uma_vez_e_usa_cache(self):
        c = _Cliente(albuns={"A1": {"id": "A1"}})
        cache = {}
        assert await retro_tags._fetch_album(c, "A1", cache) == {"id": "A1"}
        assert await retro_tags._fetch_album(c, "A1", cache) == {"id": "A1"}
        assert c.album_calls == ["A1"]

    async def test_falha_vira_none_e_tambem_fica_em_cache(self):
        c = _Cliente()
        cache = {}
        assert await retro_tags._fetch_album(c, "X", cache) is None
        assert await retro_tags._fetch_album(c, "X", cache) is None
        # Não martela a API repetindo uma busca que já falhou.
        assert c.album_calls == ["X"]


class TestResolveMetadata:
    async def test_faixa_encontrada_no_album_devolve_item_do_album(self):
        item = {"id": 7, "title": "Musica"}
        c = _Cliente(albuns={"A1": {"id": "A1", "tracks": {"items": [item]}}})
        r = await retro_tags._resolve_metadata(c, 7, "A1", {})
        assert r == (item, {"id": "A1", "tracks": {"items": [item]}}, False)
        assert c.track_calls == []

    async def test_id_da_faixa_compara_como_texto(self):
        item = {"id": 7}
        c = _Cliente(albuns={"A1": {"tracks": {"items": [item]}}})
        r = await retro_tags._resolve_metadata(c, "7", "A1", {})
        assert r[0] is item

    async def test_sem_album_id_descobre_pela_faixa(self):
        item = {"id": 7}
        c = _Cliente(
            albuns={"A1": {"id": "A1", "tracks": {"items": [item]}}},
            faixas={7: {"id": 7, "album": {"id": "A1"}}},
        )
        r = await retro_tags._resolve_metadata(c, 7, None, {})
        assert r[0] is item and r[2] is False
        assert c.track_calls == [7]

    async def test_faixa_ausente_do_album_cai_para_metadado_da_faixa(self):
        faixa = {"id": 7, "album": {"id": "A1", "title": "Disco"}}
        c = _Cliente(
            albuns={"A1": {"id": "A1", "tracks": {"items": [{"id": 99}]}}},
            faixas={7: faixa},
        )
        item, album, istrack = await retro_tags._resolve_metadata(c, 7, "A1", {})
        assert item is faixa and album == faixa["album"] and istrack is True

    async def test_album_inacessivel_cai_para_metadado_da_faixa(self):
        faixa = {"id": 7, "album": {"id": "A1"}}
        c = _Cliente(faixas={7: faixa})
        r = await retro_tags._resolve_metadata(c, 7, "A1", {})
        assert r[2] is True

    async def test_sem_nenhum_album_devolve_none(self):
        c = _Cliente(faixas={7: {"id": 7}})
        assert await retro_tags._resolve_metadata(c, 7, None, {}) is None

    async def test_faixa_inexistente_propaga_o_erro(self):
        with pytest.raises(RuntimeError):
            await retro_tags._resolve_metadata(_Cliente(), 7, None, {})


# ---------------------------------------------------------------------------
# _fetch_apple_cover_for_track
# ---------------------------------------------------------------------------
@pytest.fixture
def apple(monkeypatch):
    """Substitui a busca Apple e guarda os argumentos recebidos."""
    estado = {
        "args": None,
        "retorno": ("https://x/10000x10000bb.jpg", "Apple/iTunes", None),
    }

    async def falsa(**kw):
        estado["args"] = kw
        return estado["retorno"]

    monkeypatch.setattr("qobuz_dl.utils.get_apple_hq_cover", falsa)
    return estado


class TestFetchAppleCoverForTrack:
    async def test_monta_o_pedido_com_os_dados_da_faixa(self, apple):
        item = {
            "title": "Please Me",
            "isrc": "USX1",
            "performer": {"name": "Cardi B"},
            "album": {"title": "Please Me", "upc": "0123"},
        }
        r = await retro_tags._fetch_apple_cover_for_track(item, {}, object())
        assert r == ("https://x/10000x10000bb.jpg", "Apple/iTunes", None)
        a = apple["args"]
        assert (a["upc"], a["isrc"]) == ("0123", "USX1")
        assert (a["artist"], a["album"], a["track_title"]) == (
            "Cardi B",
            "Please Me",
            "Please Me",
        )

    async def test_junta_title_e_version(self, apple):
        item = {"title": "Fat Juicy &…", "version": "Radio Edit"}
        await retro_tags._fetch_apple_cover_for_track(item, {"title": "X"}, object())
        assert apple["args"]["track_title"] == "Fat Juicy &… (Radio Edit)"

    @pytest.mark.parametrize(
        "titulo, versao",
        [
            ("Musica (Radio Edit)", "Radio Edit"),
            ("MUSICA (RADIO EDIT)", "radio edit"),
        ],
    )
    async def test_nao_duplica_versao_que_ja_esta_no_titulo(
        self, apple, titulo, versao
    ):
        await retro_tags._fetch_apple_cover_for_track(
            {"title": titulo, "version": versao}, {}, object()
        )
        assert apple["args"]["track_title"] == titulo

    async def test_version_vazia_ou_none_e_ignorada(self, apple):
        await retro_tags._fetch_apple_cover_for_track(
            {"title": "Musica", "version": None}, {}, object()
        )
        assert apple["args"]["track_title"] == "Musica"
        await retro_tags._fetch_apple_cover_for_track(
            {"title": "Musica", "version": "  "}, {}, object()
        )
        assert apple["args"]["track_title"] == "Musica"

    async def test_artista_cai_para_o_do_album(self, apple):
        album = {"title": "Disco", "artist": {"name": "Artista do Album"}, "upc": "9"}
        await retro_tags._fetch_apple_cover_for_track({"title": "M"}, album, object())
        a = apple["args"]
        assert a["artist"] == "Artista do Album"
        assert (a["album"], a["upc"]) == ("Disco", "9")

    async def test_performer_tem_prioridade_sobre_artista_do_album(self, apple):
        album = {"artist": {"name": "Do Album"}}
        item = {"title": "M", "performer": {"name": "Da Faixa"}}
        await retro_tags._fetch_apple_cover_for_track(item, album, object())
        assert apple["args"]["artist"] == "Da Faixa"

    async def test_tudo_ausente_nao_quebra(self, apple):
        await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        a = apple["args"]
        assert (a["artist"], a["album"], a["track_title"]) == ("", "", "")

    async def test_repassa_a_sessao_http(self, apple):
        sessao = object()
        await retro_tags._fetch_apple_cover_for_track({"title": "M"}, {}, sessao)
        assert apple["args"]["session"] is sessao

    async def test_falha_devolve_o_diagnostico(self, apple):
        apple["retorno"] = (None, None, "artista incompatível (0.56 < 0.85)")
        r = await retro_tags._fetch_apple_cover_for_track({"title": "M"}, {}, object())
        assert r == (None, None, "artista incompatível (0.56 < 0.85)")

    async def test_falha_sem_motivo_ganha_motivo_padrao(self, apple):
        apple["retorno"] = (None, None, None)
        _, _, motivo = await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        assert motivo == "Apple não encontrou candidato válido"

    async def test_url_em_branco_conta_como_falha(self, apple):
        apple["retorno"] = ("   ", "Apple/iTunes", None)
        url, fonte, _ = await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        assert (url, fonte) == (None, None)

    async def test_sem_fonte_assume_apple_itunes(self, apple):
        apple["retorno"] = ("https://x/a.jpg", None, None)
        _, fonte, _ = await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        assert fonte == "Apple/iTunes"

    async def test_retorno_antigo_em_string_ainda_funciona(self, apple):
        apple["retorno"] = "https://x/a.jpg"
        r = await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        assert r == ("https://x/a.jpg", "Apple/iTunes", None)

    @pytest.mark.parametrize("retorno", [None, "", "  ", 5])
    async def test_retorno_inesperado_vira_falha(self, apple, retorno):
        apple["retorno"] = retorno
        r = await retro_tags._fetch_apple_cover_for_track({}, {}, object())
        assert r == (None, None, "Busca Apple sem URL de capa")


# ---------------------------------------------------------------------------
# _download_apple_cover
# ---------------------------------------------------------------------------
class _Resposta:
    def __init__(self, conteudo=b"\xff\xd8jpeg", tipo="image/jpeg", erro=None):
        self.content = conteudo
        self.headers = {"content-type": tipo} if tipo is not None else {}
        self._erro = erro

    def raise_for_status(self):
        if self._erro:
            raise self._erro


class _Http:
    def __init__(self, resposta=None, erro=None):
        self.resposta, self.erro, self.urls = resposta, erro, []

    async def get(self, url, **kw):
        self.urls.append((url, kw))
        if self.erro:
            raise self.erro
        return self.resposta


class TestDownloadAppleCover:
    async def test_grava_arquivo_temporario_oculto(self, tmp_path):
        http = _Http(_Resposta(b"IMG"))
        caminho = await retro_tags._download_apple_cover(
            http, "https://x/c.jpg", str(tmp_path)
        )
        assert caminho is not None
        assert os.path.dirname(caminho) == str(tmp_path)
        nome = os.path.basename(caminho)
        assert nome.startswith(".apple-cover-") and nome.endswith(".jpg")
        with open(caminho, "rb") as f:
            assert f.read() == b"IMG"
        assert http.urls[0][1]["follow_redirects"] is True

    async def test_content_type_ausente_e_aceito(self, tmp_path):
        http = _Http(_Resposta(b"IMG", tipo=None))
        assert await retro_tags._download_apple_cover(http, "u", str(tmp_path))

    async def test_content_type_que_nao_e_imagem_e_rejeitado(self, tmp_path):
        http = _Http(_Resposta(b"<html>", tipo="text/html"))
        assert await retro_tags._download_apple_cover(http, "u", str(tmp_path)) is None
        assert os.listdir(tmp_path) == []

    async def test_corpo_vazio_e_rejeitado(self, tmp_path):
        http = _Http(_Resposta(b""))
        assert await retro_tags._download_apple_cover(http, "u", str(tmp_path)) is None
        assert os.listdir(tmp_path) == []

    async def test_erro_http_devolve_none_sem_deixar_lixo(self, tmp_path):
        http = _Http(_Resposta(erro=RuntimeError("HTTP 500")))
        assert await retro_tags._download_apple_cover(http, "u", str(tmp_path)) is None
        assert os.listdir(tmp_path) == []

    async def test_erro_de_rede_devolve_none(self, tmp_path):
        http = _Http(erro=ConnectionError("caiu"))
        assert await retro_tags._download_apple_cover(http, "u", str(tmp_path)) is None

    async def test_pasta_inexistente_devolve_none(self, tmp_path):
        http = _Http(_Resposta())
        r = await retro_tags._download_apple_cover(
            http, "u", str(tmp_path / "nao-existe")
        )
        assert r is None
