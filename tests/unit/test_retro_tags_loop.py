"""Testa o laço do comando `tags` (`retag_directory`), o nome exibido por
faixa e as leituras de tag.

`test_retro_tags.py` cobre contadores básicos e `test_retro_tags_helpers.py`
as peças pequenas (busca Apple, download temporário). Aqui ficam os DESFECHOS
de cada arquivo no laço, um por teste:

* capa Apple encontrada, baixada e MAIOR que a atual -> embutida; temporário
  removido;
* candidata que não supera a capa atual -> capa preservada;
* nenhum candidato / download falhou / exceção na etapa Apple -> as tags são
  regravadas mesmo assim e a capa atual é mantida;
* rótulo de ação por arquivo (tags e/ou capa alteradas, ou nenhuma);
* erro ao gravar tags / ao consultar o Qobuz -> conta erro e segue;
* a sessão HTTP é sempre fechada e o resumo final bate com os contadores.

Nenhum teste usa rede, mutagen real nem arquivo de áudio de verdade: as
leituras de tag usam classes falsas no lugar de `FLAC`/`id3`.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from qobuz_dl import retro_tags

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# extract_album_id / snapshot_tags com mutagen falso
# ---------------------------------------------------------------------------
class _FlacFalso:
    """Imita o que o código usa de `mutagen.flac.FLAC`."""

    registro = {}

    def __init__(self, caminho):
        dados = self.registro[caminho]
        if isinstance(dados, Exception):
            raise dados
        self._tags = dados.get("tags", {})
        self.pictures = dados.get("pictures", [])

    @property
    def tags(self):
        return self._tags or None

    def get(self, chave, padrao=None):
        return self._tags.get(chave, padrao)


class _Frame:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Id3Falso:
    registro = {}

    def __init__(self, caminho):
        dados = self.registro[caminho]
        if isinstance(dados, Exception):
            raise dados
        self._frames = dados

    def getall(self, nome):
        return [f for k, f in self._frames.items() if k.startswith(nome)]

    def items(self):
        return list(self._frames.items())


@pytest.fixture
def mutagen_falso(monkeypatch):
    _FlacFalso.registro = {}
    _Id3Falso.registro = {}
    monkeypatch.setattr(retro_tags, "FLAC", _FlacFalso)
    monkeypatch.setattr(retro_tags.id3, "ID3", _Id3Falso, raising=False)
    return SimpleNamespace(flac=_FlacFalso.registro, id3=_Id3Falso.registro)


class TestExtractAlbumId:
    def test_flac_tag_principal(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {"tags": {"QOBUZALBUMID": [" ABC123 "]}}
        assert retro_tags.extract_album_id("a.flac") == "ABC123"

    def test_flac_ordem_de_prioridade_das_tags(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {
            "tags": {"ALBUM_ID": ["C"], "QOBUZ ALBUM ID": ["B"]}
        }
        assert retro_tags.extract_album_id("a.flac") == "B"

    def test_flac_tag_vazia_e_pulada(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {
            "tags": {"QOBUZALBUMID": ["  "], "ALBUM_ID": ["X9"]}
        }
        assert retro_tags.extract_album_id("a.flac") == "X9"

    def test_flac_id_vem_da_url_no_comentario(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {
            "tags": {
                "COMMENT": ["outra coisa", "https://www.qobuz.com/album/q1w2e3 ok"]
            }
        }
        assert retro_tags.extract_album_id("a.flac") == "q1w2e3"

    def test_flac_sem_nenhum_id(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {"tags": {"TITLE": ["x"]}}
        assert retro_tags.extract_album_id("a.flac") is None

    def test_mp3_txxx_ignora_caixa_espacos_e_sublinhado(self, mutagen_falso):
        mutagen_falso.id3["a.mp3"] = {
            "TXXX:0": _Frame(desc="outra", text=["x"]),
            "TXXX:1": _Frame(desc="Qobuz_Album Id", text=[" Z77 "]),
        }
        assert retro_tags.extract_album_id("a.mp3") == "Z77"

    def test_mp3_frame_sem_texto_e_pulado(self, mutagen_falso):
        mutagen_falso.id3["a.mp3"] = {
            "TXXX:0": _Frame(desc="QOBUZALBUMID", text=[]),
            "TXXX:1": _Frame(desc="QOBUZALBUMID", text=["OK1"]),
        }
        assert retro_tags.extract_album_id("a.mp3") == "OK1"

    def test_mp3_sem_id(self, mutagen_falso):
        mutagen_falso.id3["a.mp3"] = {}
        assert retro_tags.extract_album_id("a.mp3") is None

    def test_extensao_nao_suportada(self, mutagen_falso):
        assert retro_tags.extract_album_id("a.ogg") is None

    @pytest.mark.parametrize("arquivo", ["a.flac", "a.mp3"])
    def test_erro_de_leitura_vira_none(self, mutagen_falso, arquivo):
        mutagen_falso.flac["a.flac"] = OSError("corrompido")
        mutagen_falso.id3["a.mp3"] = OSError("corrompido")
        assert retro_tags.extract_album_id(arquivo) is None

    def test_extensao_em_maiuscula(self, mutagen_falso):
        mutagen_falso.flac["A.FLAC"] = {"tags": {"QOBUZALBUMID": ["M1"]}}
        assert retro_tags.extract_album_id("A.FLAC") == "M1"


class TestSnapshotTags:
    def test_flac_texto_em_maiuscula_e_capas(self, mutagen_falso):
        foto = SimpleNamespace(type=3, mime="image/jpeg", desc="", data=b"12345")
        mutagen_falso.flac["a.flac"] = {
            "tags": {"title": ["Musica"], "Artist": ["A", "B"]},
            "pictures": [foto],
        }
        snap = retro_tags.snapshot_tags("a.flac")
        assert snap["text"] == {"TITLE": ["Musica"], "ARTIST": ["A", "B"]}
        assert snap["art"] == [(3, "image/jpeg", "", 5)]

    def test_flac_sem_tags(self, mutagen_falso):
        mutagen_falso.flac["a.flac"] = {}
        assert retro_tags.snapshot_tags("a.flac") == {"text": {}, "art": []}

    def test_mp3_ignora_apic_no_texto_e_lista_a_capa_a_parte(self, mutagen_falso):
        mutagen_falso.id3["a.mp3"] = {
            "TIT2": _Frame(text=["Musica"]),
            "APIC:": _Frame(mime="image/png", type=3, desc="c", data=b"abc"),
        }
        snap = retro_tags.snapshot_tags("a.mp3")
        assert snap["text"] == {"TIT2": ["Musica"]}
        assert snap["art"] == [("image/png", 3, "c", 3)]

    def test_mp3_frame_sem_texto_usa_repr(self, mutagen_falso):
        class SemTexto:
            def __repr__(self):
                return "<frame>"

        mutagen_falso.id3["a.mp3"] = {"WXXX": SemTexto()}
        assert retro_tags.snapshot_tags("a.mp3")["text"] == {"WXXX": ["<frame>"]}

    @pytest.mark.parametrize("arquivo", ["a.flac", "a.mp3"])
    def test_erro_de_leitura_devolve_snapshot_vazio(self, mutagen_falso, arquivo):
        mutagen_falso.flac["a.flac"] = OSError("x")
        mutagen_falso.id3["a.mp3"] = OSError("x")
        assert retro_tags.snapshot_tags(arquivo) == {"text": {}, "art": []}

    def test_extensao_nao_suportada(self, mutagen_falso):
        assert retro_tags.snapshot_tags("a.wav") == {"text": {}, "art": []}

    def test_mudar_so_a_capa_muda_o_snapshot(self, mutagen_falso):
        foto = SimpleNamespace(type=3, mime="image/jpeg", desc="", data=b"1")
        mutagen_falso.flac["a.flac"] = {"tags": {"TITLE": ["x"]}, "pictures": [foto]}
        antes = retro_tags.snapshot_tags("a.flac")
        foto2 = SimpleNamespace(type=3, mime="image/jpeg", desc="", data=b"123456")
        mutagen_falso.flac["a.flac"] = {"tags": {"TITLE": ["x"]}, "pictures": [foto2]}
        assert retro_tags.snapshot_tags("a.flac") != antes


# ---------------------------------------------------------------------------
# Nome exibido por faixa
# ---------------------------------------------------------------------------
def _nome(item=None, album=None, arquivo="/m/01 - Faixa.flac", **kw):
    return retro_tags._display_track_name(item, album, arquivo, **kw)


class TestDisplayTrackName:
    def test_artista_e_titulo(self):
        item = {"performer": {"name": "Daft Punk"}, "title": "One More Time"}
        assert _nome(item, {}) == "Daft Punk - One More Time"

    def test_versao_e_anexada_ao_titulo(self):
        item = {
            "performer": {"name": "A"},
            "title": "Fat Juicy &…",
            "version": "Radio Edit",
        }
        assert _nome(item, {}) == "A - Fat Juicy &… (Radio Edit)"

    @pytest.mark.parametrize(
        "titulo, versao",
        [("Musica (Radio Edit)", "Radio Edit"), ("MUSICA (RADIO EDIT)", "radio edit")],
    )
    def test_versao_ja_presente_nao_duplica(self, titulo, versao):
        item = {"performer": {"name": "A"}, "title": titulo, "version": versao}
        assert _nome(item, {}) == f"A - {titulo}"

    def test_so_a_versao_vira_o_titulo(self):
        item = {"performer": {"name": "A"}, "version": "Remastered"}
        assert _nome(item, {}) == "A - Remastered"

    def test_artista_cai_para_o_do_album_do_item(self):
        item = {"title": "M", "album": {"artist": {"name": "Do Album"}}}
        assert _nome(item, {}) == "Do Album - M"

    def test_artista_cai_para_o_do_album_passado(self):
        assert _nome({"title": "M"}, {"artist": {"name": "Do Album"}}) == "Do Album - M"

    def test_performer_tem_prioridade_sobre_o_album(self):
        item = {"title": "M", "performer": {"name": "Faixa"}}
        assert _nome(item, {"artist": {"name": "Album"}}) == "Faixa - M"

    def test_performer_dict_usa_title_quando_nao_ha_name(self):
        assert _nome({"title": "M", "performer": {"title": "Alt"}}, {}) == "Alt - M"

    def test_performer_em_lista_mistura_dicts_e_textos(self):
        item = {"title": "M", "performer": [{"name": "A"}, "B", None, {"name": " C "}]}
        assert _nome(item, {}) == "A, B, C - M"

    def test_performer_em_tupla(self):
        assert _nome({"title": "M", "performer": ("A", "B")}, {}) == "A, B - M"

    def test_performer_em_texto(self):
        assert _nome({"title": "M", "performer": "  Solo  "}, {}) == "Solo - M"

    def test_artista_do_album_em_texto(self):
        assert _nome({"title": "M"}, {"artist": "Texto"}) == "Texto - M"

    def test_sem_artista_mostra_so_o_titulo(self):
        assert _nome({"title": "M"}, {}) == "M"

    def test_sem_titulo_usa_o_arquivo_sem_prefixo_nem_extensao(self):
        item = {"performer": {"name": "A"}}
        assert _nome(item, {}, "/m/CD 01/07 - Minha Musica.flac") == "A - Minha Musica"

    @pytest.mark.parametrize(
        "arquivo, esperado",
        [
            ("/m/01 Faixa.mp3", "Faixa"),
            ("/m/1_Faixa.mp3", "Faixa"),
            ("/m/101. Faixa.mp3", "Faixa"),
            ("/m/Faixa.mp3", "Faixa"),
            ("/m/1984.mp3", "1984"),
        ],
    )
    def test_prefixos_numericos_do_arquivo(self, arquivo, esperado):
        assert _nome({}, {}, arquivo) == esperado

    def test_espacos_repetidos_sao_colapsados(self):
        item = {"performer": {"name": "A   B"}, "title": "  M \n  N "}
        assert _nome(item, {}) == "A B - M N"

    def test_titulo_longo_e_truncado_com_reticencias(self):
        item = {"performer": {"name": "Artista"}, "title": "x" * 100}
        nome = _nome(item, {})
        assert len(nome) == 54 and nome.endswith("…")
        assert nome.startswith("Artista - xxx")

    def test_limite_personalizado(self):
        nome = _nome({"performer": {"name": "A"}, "title": "abcdefghij"}, {}, limit=8)
        assert nome == "A - abc…"

    def test_exatamente_no_limite_nao_trunca(self):
        item = {"title": "x" * 54}
        assert _nome(item, {}) == "x" * 54

    def test_item_e_album_nulos_nao_quebram(self):
        assert _nome(None, None, "/m/05 - Arquivo.flac") == "Arquivo"

    def test_sem_nada_devolve_o_nome_do_arquivo(self):
        assert _nome({}, {}, "/m/---.flac") == "---"

    def test_sem_pastas_no_resultado(self):
        nome = _nome({"title": "M"}, {}, "/musicas/CD 02/Album/03 - M.flac")
        assert "/" not in nome and "CD" not in nome


# ---------------------------------------------------------------------------
# Ambiente do laço
# ---------------------------------------------------------------------------
class _Sessao:
    fechada = 0
    criadas = 0

    def __init__(self, *a, **kw):
        type(self).criadas += 1

    async def aclose(self):
        type(self).fechada += 1


SEM_DECISAO = {"url": None, "source": None, "method": None, "note": None}


class Ambiente:
    """Controla cada ponto de decisão do laço; todos os "fakes" vivem aqui."""

    def __init__(self, pasta):
        self.pasta = pasta
        self.ids = {}
        self.album_ids = {}
        self.item = {"title": "Musica", "performer": {"name": "Banda"}}
        self.apple = ("https://img/a.jpg", "Apple/iTunes", None)
        self.baixar_bytes = 3072  # None = download falha
        self.substituir = True  # resultado de should_replace_embedded_cover
        self.resolver = "ok"  # "ok" | "nada" | Exception
        self.erro_ao_gravar = None
        self.erro_so_em = None  # None = todos; ou conjunto de nomes de arquivo
        self.muda_texto = True
        self.muda_capa = True
        self.chamadas = []
        self.estado = {}  # caminho -> {"texto": n, "capa": n}
        self.linhas = []
        # Álbum Qobuz devolvido pela resolução (por faixa, ou o padrão) e
        # decisão de capa por álbum (por ID de álbum; padrão: sem decisão,
        # ou seja, cada faixa é avaliada sozinha).
        self.album = {"id": "ALB"}
        self.album_de = {}  # track_id -> álbum
        self.decisao_de = {}  # ID do álbum -> decisão
        self.decisoes = []  # IDs de álbum para os quais houve decisão

    def arquivo(self, nome, track_id="1"):
        p = self.pasta / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        self.ids[os.path.basename(nome)] = track_id
        return p

    @property
    def texto(self):
        return "\n".join(t for _, t in self.linhas)

    def de(self, tipo):
        return [t for k, t in self.linhas if k == tipo]

    def tags(self, nome):
        return [c for c in self.chamadas if c[0] == nome]


@pytest.fixture
def amb(monkeypatch, tmp_path):
    a = Ambiente(tmp_path)
    _Sessao.fechada = _Sessao.criadas = 0

    # --- identificação e Qobuz ---------------------------------------------
    monkeypatch.setattr(
        retro_tags, "extract_track_id", lambda p: a.ids.get(os.path.basename(p))
    )
    monkeypatch.setattr(
        retro_tags, "extract_album_id", lambda p: a.album_ids.get(os.path.basename(p))
    )

    async def resolver(client, track_id, album_id, cache):
        if isinstance(a.resolver, Exception):
            raise a.resolver
        if a.resolver == "nada":
            return None
        album = a.album_de.get(str(track_id), a.album)
        return (dict(a.item, id=track_id), dict(album), False)

    monkeypatch.setattr(retro_tags, "_resolve_metadata", resolver)

    # --- Apple --------------------------------------------------------------
    async def buscar(item, album, sessao):
        if isinstance(a.apple, Exception):
            raise a.apple
        return a.apple

    async def baixar(sessao, url, pasta):
        if a.baixar_bytes is None:
            return None
        caminho = os.path.join(pasta, ".apple-cover-teste.jpg")
        with open(caminho, "wb") as f:
            f.write(b"x" * a.baixar_bytes)
        a.chamadas.append(("download", url))
        return caminho

    async def decidir(album, sessao, resultados):
        a.decisoes.append(album.get("id"))
        base = a.decisao_de.get(album.get("id"), SEM_DECISAO)
        return dict(base, path=None, used=False)

    monkeypatch.setattr(retro_tags, "_fetch_apple_cover_for_track", buscar)
    monkeypatch.setattr(retro_tags, "_decide_album_cover", decidir)
    monkeypatch.setenv("COLUMNS", "120")  # o log quebra linhas pela largura
    monkeypatch.setattr(retro_tags, "_download_apple_cover", baixar)
    monkeypatch.setattr(retro_tags, "make_client", lambda **kw: _Sessao())
    monkeypatch.setattr(
        retro_tags.metadata,
        "should_replace_embedded_cover",
        lambda arquivo, candidata: a.substituir,
    )

    # --- gravação de tags e snapshot ------------------------------------------
    def fazer_tag(nome):
        def tag(filename, root, final, item, album, istrack, em_image, **kw):
            if a.erro_ao_gravar and (
                a.erro_so_em is None or os.path.basename(filename) in a.erro_so_em
            ):
                raise a.erro_ao_gravar
            a.chamadas.append(
                (
                    nome,
                    os.path.basename(filename),
                    em_image,
                    kw.get("embed_cover_path"),
                    kw.get("cover_source"),
                )
            )
            est = a.estado.setdefault(filename, {"texto": 0, "capa": 0})
            if a.muda_texto:
                est["texto"] += 1
            if a.muda_capa and kw.get("embed_cover_path"):
                est["capa"] += 1

        return tag

    monkeypatch.setattr(retro_tags.metadata, "tag_flac", fazer_tag("flac"))
    monkeypatch.setattr(retro_tags.metadata, "tag_mp3", fazer_tag("mp3"))

    def snapshot(caminho):
        est = a.estado.get(caminho, {"texto": 0, "capa": 0})
        return {"text": {"v": est["texto"]}, "art": [("capa", est["capa"])]}

    monkeypatch.setattr(retro_tags, "snapshot_tags", snapshot)

    # --- saída --------------------------------------------------------------
    for tipo in ("emit", "ok", "skip", "error", "warn", "detail", "step"):
        monkeypatch.setattr(
            retro_tags.ui,
            tipo,
            lambda msg="", *x, _t=tipo, **k: a.linhas.append((_t, str(msg))),
        )
    return a


def _rodar(amb, multi=True):
    return asyncio.run(
        retro_tags.retag_directory(
            str(amb.pasta), SimpleNamespace(), SimpleNamespace(multi_value_tags=multi)
        )
    )


def _temporarios(pasta):
    return [p for p in os.listdir(pasta) if p.startswith(".apple-cover")]


# ---------------------------------------------------------------------------
# Desfechos da etapa Apple
# ---------------------------------------------------------------------------
class TestCapaAppleNoLaco:
    def test_capa_encontrada_e_embutida(self, amb):
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_embedded"] == 1 and stats["updated"] == 1
        _, _, em_image, caminho, origem = amb.tags("flac")[0]
        assert em_image is True
        assert caminho.endswith(".apple-cover-teste.jpg")
        assert origem == "Apple/iTunes"
        assert "Fonte: Apple/iTunes | capa baixada (3 KiB)" in amb.texto

    def test_temporario_e_removido_depois_de_gravar(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        assert _temporarios(amb.pasta) == []

    def test_fonte_ausente_assume_apple_itunes(self, amb):
        amb.apple = ("https://img/a.jpg", None, None)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert amb.tags("flac")[0][4] == "Apple/iTunes"

    def test_fonte_informada_e_respeitada(self, amb):
        amb.apple = ("https://img/a.jpg", "Apple/Music", None)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert amb.tags("flac")[0][4] == "Apple/Music"

    def test_tamanho_minimo_de_1_kib(self, amb):
        amb.baixar_bytes = 10
        amb.arquivo("a.flac")
        _rodar(amb)
        assert "capa baixada (1 KiB)" in amb.texto

    def test_nenhum_candidato_com_diagnostico(self, amb):
        amb.apple = (None, None, "artista incompatível (0.56 < 0.85)")
        amb.muda_capa = False
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_not_found"] == 1 and stats["apple_embedded"] == 0
        assert "Motivo: artista incompatível (0.56 < 0.85)" in amb.texto
        _, _, em_image, caminho, origem = amb.tags("flac")[0]
        assert (em_image, caminho, origem) == (False, None, None)
        # As tags são regravadas mesmo sem capa nova.
        assert stats["updated"] == 1

    def test_nenhum_candidato_sem_diagnostico(self, amb):
        amb.apple = (None, None, None)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert "Motivo: nenhum candidato passou pelos filtros" in amb.texto

    @pytest.mark.parametrize("url", ["", "   ", None, 123])
    def test_url_invalida_conta_como_nao_encontrada(self, amb, url):
        amb.apple = (url, "Apple/iTunes", None)
        amb.arquivo("a.flac")
        assert _rodar(amb)["apple_not_found"] == 1

    def test_download_falhou(self, amb):
        amb.baixar_bytes = None
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_failed"] == 1 and stats["apple_embedded"] == 0
        assert "a imagem encontrada na Apple não pôde ser baixada" in amb.texto
        assert amb.tags("flac")[0][2] is False

    def test_excecao_na_busca_nao_impede_o_retag(self, amb):
        amb.apple = RuntimeError("quebrou")
        amb.muda_capa = False
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_failed"] == 1 and stats["errors"] == 0
        assert stats["updated"] == 1
        assert "erro na etapa da Apple" in amb.texto
        assert "RuntimeError: quebrou" in amb.texto


class TestPreservacaoDaCapaAtual:
    def test_candidata_nao_superior_e_descartada(self, amb):
        amb.substituir = False
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_embedded"] == 0
        _, _, em_image, caminho, origem = amb.tags("flac")[0]
        assert (em_image, caminho, origem) == (False, None, None)
        assert "mantida -- a capa atual já tem resolução igual ou maior" in amb.texto

    def test_comparacao_impossivel_nao_diz_que_a_atual_e_melhor(self, amb, monkeypatch):
        amb.substituir = False
        monkeypatch.setattr(
            retro_tags.metadata, "last_cover_compare_reason", lambda: "unreadable"
        )
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_embedded"] == 0
        _, _, em_image, caminho, origem = amb.tags("flac")[0]
        assert (em_image, caminho, origem) == (False, None, None)
        assert "não deu para comparar as resoluções" in amb.texto
        assert "igual ou maior" not in amb.texto
        assert _temporarios(amb.pasta) == []

    def test_temporario_da_candidata_descartada_e_removido(self, amb):
        amb.substituir = False
        amb.arquivo("a.flac")
        _rodar(amb)
        assert _temporarios(amb.pasta) == []

    def test_nao_conta_como_capa_apple_embutida_nem_como_falha(self, amb):
        amb.substituir = False
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_failed"] == 0 and stats["apple_not_found"] == 0

    def test_comparacao_recebe_o_arquivo_e_a_candidata(self, amb, monkeypatch):
        visto = {}

        def comparar(arquivo, candidata):
            visto["arquivo"], visto["candidata"] = arquivo, candidata
            return True

        monkeypatch.setattr(
            retro_tags.metadata, "should_replace_embedded_cover", comparar
        )
        amb.arquivo("a.flac")
        _rodar(amb)
        assert os.path.basename(visto["arquivo"]) == "a.flac"
        assert os.path.basename(visto["candidata"]) == ".apple-cover-teste.jpg"

    def test_sem_candidata_nao_chama_a_comparacao(self, amb, monkeypatch):
        chamadas = []
        monkeypatch.setattr(
            retro_tags.metadata,
            "should_replace_embedded_cover",
            lambda *a: chamadas.append(a) or True,
        )
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        _rodar(amb)
        assert chamadas == []

    def test_falha_ao_remover_a_candidata_descartada_nao_derruba(
        self, amb, monkeypatch
    ):
        amb.substituir = False
        amb.arquivo("a.flac")
        real = os.remove

        def remove_falha(caminho):
            if ".apple-cover" in str(caminho):
                raise OSError("travado")
            return real(caminho)

        monkeypatch.setattr(retro_tags.os, "remove", remove_falha)
        stats = _rodar(amb)
        assert stats["errors"] == 0 and stats["updated"] == 1


# ---------------------------------------------------------------------------
# Rótulo de ação por arquivo
# ---------------------------------------------------------------------------
def _bloco(amb, prefixo="[1/1]"):
    """Linhas emitidas do bloco do arquivo (do cabeçalho até a linha em branco)."""
    emit = amb.de("emit")
    inicio = next(i for i, t in enumerate(emit) if t.startswith(prefixo))
    bloco = []
    for texto in emit[inicio:]:
        if texto == "" and bloco:
            break
        bloco.append(texto)
    return bloco


class TestRotuloDeAcao:
    def test_tags_e_capa(self, amb):
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert _bloco(amb)[1] == "   • Alterado -- tags e capa"
        assert stats["updated"] == 1 and stats["unchanged"] == 0

    def test_somente_tags(self, amb):
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        # A capa não foi achada: a alteração das tags ficou com pendência.
        assert _bloco(amb)[1] == (
            "   • Alterado (somente as tags), mas ficou uma pendência na capa"
        )
        assert stats["updated"] == 1

    def test_somente_tags_com_capa_preservada_nao_tem_pendencia(self, amb):
        amb.substituir = False
        amb.arquivo("a.flac")
        _rodar(amb)
        assert _bloco(amb)[1] == "   • Alterado -- somente as tags"

    def test_somente_capa(self, amb):
        amb.muda_texto = False
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert _bloco(amb)[1] == "   • Alterado -- somente a capa"
        assert stats["updated"] == 1

    def test_nenhuma_alteracao(self, amb):
        amb.muda_texto = amb.muda_capa = False
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert _bloco(amb)[1] == "   • Sem alterações"
        assert stats["unchanged"] == 1 and stats["updated"] == 0

    def test_linhas_de_detalhe_de_tags_e_capa(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = _bloco(amb)
        assert "   Tags   alteradas (1 campo)" in bloco
        assert "   Capa   substituída pela capa da Apple/iTunes" in bloco
        assert any("Fonte: Apple/iTunes | capa baixada" in t for t in bloco)

    def test_detalhe_sem_alteracao(self, amb):
        amb.muda_texto = amb.muda_capa = False
        amb.apple = (None, None, "motivo-xyz")
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = _bloco(amb)
        assert "   Tags   já estavam corretas" in bloco
        assert "   Capa   não encontrada na Apple -- a capa atual foi mantida" in bloco
        assert any("Motivo: motivo-xyz" in t for t in bloco)

    def test_capa_mantida_porque_a_atual_ja_e_igual_ou_maior(self, amb):
        amb.muda_texto = amb.muda_capa = False
        amb.substituir = False
        amb.arquivo("a.flac")
        _rodar(amb)
        assert (
            "   Capa   mantida -- a capa atual já tem resolução igual ou maior "
            "que a da Apple"
        ) in _bloco(amb)

    def test_snapshot_em_formato_de_mapa_simples_tambem_funciona(
        self, amb, monkeypatch
    ):
        # Chamadores antigos devolvem o mapa de tags direto, sem "text"/"art".
        def snapshot(caminho):
            est = amb.estado.get(caminho, {"texto": 0})
            return {"ARTIST": [str(est["texto"])]}

        monkeypatch.setattr(retro_tags, "snapshot_tags", snapshot)
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        assert _rodar(amb)["updated"] == 1

    def test_so_a_linha_principal_leva_o_nome_exibido(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        assert _bloco(amb)[0] == "[1/1] Banda - Musica"


# ---------------------------------------------------------------------------
# Outros desfechos por arquivo
# ---------------------------------------------------------------------------
class TestDesfechosPorArquivo:
    def test_sem_id_qobuz_mostra_o_nome_do_arquivo(self, amb):
        amb.arquivo(os.path.join("CD 02", "07 - Faixa.flac"), track_id=None)
        stats = _rodar(amb)
        assert stats["no_id"] == 1 and stats["updated"] == 0
        assert "07 - Faixa.flac (sem ID Qobuz nas tags)" in amb.texto
        assert amb.chamadas == []

    def test_faixa_nao_encontrada_no_qobuz(self, amb):
        amb.resolver = "nada"
        amb.arquivo("a.flac", track_id="77")
        stats = _rodar(amb)
        assert stats["not_found"] == 1
        assert "a.flac (faixa 77 não encontrada no Qobuz)" in amb.texto

    def test_erro_ao_consultar_o_qobuz(self, amb):
        amb.resolver = RuntimeError("503")
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["errors"] == 1
        assert "erro ao consultar o Qobuz: 503" in amb.texto
        assert amb.chamadas == []  # nem chegou na etapa Apple

    def test_erro_ao_gravar_tags_usa_o_nome_exibido_e_remove_o_temporario(self, amb):
        amb.erro_ao_gravar = OSError("disco cheio")
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["errors"] == 1 and stats["updated"] == 0
        assert "Banda - Musica (erro ao gravar tags: disco cheio)" in amb.texto
        assert _temporarios(amb.pasta) == []
        assert stats["apple_embedded"] == 0

    def test_erro_em_um_arquivo_nao_para_os_outros(self, amb):
        amb.erro_ao_gravar = OSError("primeiro falha")
        amb.erro_so_em = {"a.flac"}
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        stats = _rodar(amb)
        assert stats["errors"] == 1 and stats["updated"] == 1
        assert [c[1] for c in amb.tags("flac")] == ["b.flac"]

    def test_mp3_usa_tag_mp3(self, amb):
        amb.arquivo("a.mp3")
        _rodar(amb)
        assert amb.tags("mp3") and not amb.tags("flac")

    def test_extensao_em_maiuscula_usa_o_tagger_certo(self, amb):
        amb.arquivo("a.MP3")
        _rodar(amb)
        assert amb.tags("mp3")

    def test_falha_ao_remover_temporario_depois_de_gravar_nao_derruba(
        self, amb, monkeypatch
    ):
        amb.arquivo("a.flac")
        real = os.remove

        def remove_falha(caminho):
            if ".apple-cover" in str(caminho):
                raise OSError("travado")
            return real(caminho)

        monkeypatch.setattr(retro_tags.os, "remove", remove_falha)
        stats = _rodar(amb)
        assert stats["updated"] == 1 and stats["errors"] == 0


# ---------------------------------------------------------------------------
# Pasta, sessão e resumo
# ---------------------------------------------------------------------------
class TestPastaSessaoEResumo:
    def test_pasta_sem_audio(self, amb):
        stats = _rodar(amb)
        assert stats["total"] == 0
        assert "Nenhum arquivo .flac/.mp3 encontrado" in amb.texto
        # Nem chegou a abrir a sessão HTTP.
        assert _Sessao.criadas == 0

    def test_pasta_inexistente_devolve_none(self, amb, tmp_path):
        r = asyncio.run(
            retro_tags.retag_directory(
                str(tmp_path / "nada"),
                SimpleNamespace(),
                SimpleNamespace(multi_value_tags=True),
            )
        )
        assert r is None
        assert "A pasta não existe no dispositivo" in amb.texto

    def test_sessao_http_e_aberta_e_fechada_uma_vez(self, amb):
        amb.arquivo("a.flac")
        amb.arquivo("b.flac", track_id="2")
        _rodar(amb)
        assert (_Sessao.criadas, _Sessao.fechada) == (1, 1)

    def test_sessao_e_criada_com_redirects_e_user_agent(self, amb, monkeypatch):
        recebido = {}

        def fabrica(**kw):
            recebido.update(kw)
            return _Sessao()

        monkeypatch.setattr(retro_tags, "make_client", fabrica)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert recebido["follow_redirects"] is True
        assert "Mozilla" in recebido["headers"]["User-Agent"]

    def test_sessao_e_fechada_mesmo_com_excecao_inesperada(self, amb, monkeypatch):
        amb.arquivo("a.flac")

        def explode(*a, **k):
            raise KeyboardInterrupt()

        monkeypatch.setattr(retro_tags, "snapshot_tags", explode)
        with pytest.raises(KeyboardInterrupt):
            _rodar(amb)
        assert _Sessao.fechada == 1

    def test_cabecalho_mostra_pasta_e_multi_tags(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb, multi=True)
        assert "Multi-tags" in amb.texto and "ATIVADO" in amb.texto
        assert str(amb.pasta) in amb.texto
        assert "Capas Apple" in amb.texto

    def test_cabecalho_multi_tags_desativado(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb, multi=False)
        assert "DESATIVADO" in amb.texto

    def test_resumo_final_bate_com_os_contadores(self, amb):
        amb.arquivo("a.flac", "1")
        amb.arquivo("b.flac", "2")
        amb.arquivo("c.flac", None)
        stats = _rodar(amb)
        resumo = amb.texto
        assert f"Arquivos analisados       : {stats['total']}" in resumo
        assert "Atualizados               : 2" in resumo
        assert "Sem ID Qobuz              : 1" in resumo
        assert "Capas Apple embutidas     : 2" in resumo
        assert "Erros                     : 0" in resumo
        assert "Arquivos sem ID Qobuz não podem ser corrigidos" in resumo

    def test_aviso_de_sem_id_so_aparece_quando_necessario(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        assert "não podem ser corrigidos" not in amb.texto

    def test_rotulo_de_progresso_tem_largura_fixa(self, amb):
        for i in range(10):
            amb.arquivo(f"f{i:02}.flac", str(i + 1))
        _rodar(amb)
        rotulos = [t for t in amb.de("emit") if "/10]" in t]
        assert any(t.startswith("[01/10]") for t in rotulos)
        assert any(t.startswith("[10/10]") for t in rotulos)

    def test_ids_do_album_sao_repassados(self, amb, monkeypatch):
        visto = {}

        async def resolver(client, track_id, album_id, cache):
            visto["album_id"] = album_id
            return ({"id": track_id}, {"id": "ALB"}, False)

        monkeypatch.setattr(retro_tags, "_resolve_metadata", resolver)
        amb.arquivo("a.flac")
        amb.album_ids["a.flac"] = "ALB77"
        _rodar(amb)
        assert visto["album_id"] == "ALB77"

    def test_arquivos_em_subpastas_sao_processados(self, amb):
        amb.arquivo(os.path.join("CD 02", "e.flac"))
        assert _rodar(amb)["updated"] == 1


# ---------------------------------------------------------------------------
# Diff exato das tags alteradas
# ---------------------------------------------------------------------------
class TestFormatTagDiff:
    def _diff(self, antes, depois, **kw):
        return retro_tags.format_tag_diff(antes, depois, **kw)

    def test_sem_diferenca(self):
        assert self._diff({"A": ["x"]}, {"A": ["x"]}) == []

    def test_valor_simples_alterado(self):
        (linha,) = self._diff({"ALBUM": ["Velho"]}, {"ALBUM": ["Novo"]})
        assert "~ ALBUM:" in linha and "'Velho' → 'Novo'" in linha

    def test_tag_nova_e_tag_removida(self):
        linhas = self._diff({"VELHA": ["a"]}, {"NOVA": ["b"]})
        assert any("+ NOVA:" in ln and "b" in ln for ln in linhas)
        assert any("- VELHA:" in ln and "a" in ln for ln in linhas)

    def test_comentario_multilinha_mostra_so_as_linhas_que_mudaram(self):
        antes = {"COMMENT": ["Fonte: Qobuz\nTipo: Album\nCapa: Apple/iTunes"]}
        depois = {"COMMENT": ["Fonte: Qobuz\nTipo: Single"]}
        linhas = self._diff(antes, depois)
        assert any("~ COMMENT:" in ln for ln in linhas)
        assert any("- Tipo: Album" in ln for ln in linhas)
        assert any("- Capa: Apple/iTunes" in ln for ln in linhas)
        assert any("+ Tipo: Single" in ln for ln in linhas)
        assert not any("Fonte: Qobuz" in ln for ln in linhas)

    def test_multivalor_mostra_o_que_saiu_e_o_que_entrou(self):
        linhas = self._diff({"ARTIST": ["A", "B"]}, {"ARTIST": ["A, B"]})
        assert any("- A" in ln for ln in linhas)
        assert any("- B" in ln for ln in linhas)
        assert any("+ A, B" in ln for ln in linhas)

    def test_valores_nao_lista_nem_texto_nao_quebram(self):
        (linha,) = self._diff({"v": 0}, {"v": 1})
        assert "'0' → '1'" in linha

    def test_valor_longo_e_truncado(self):
        (linha,) = self._diff({"K": ["a"]}, {"K": ["b" * 300]}, max_value=20)
        assert "…" in linha and "b" * 30 not in linha

    def test_limite_de_linhas(self):
        antes = {f"K{i:02}": ["a"] for i in range(10)}
        depois = {f"K{i:02}": ["b"] for i in range(10)}
        linhas = self._diff(antes, depois, max_lines=3)
        assert len(linhas) == 4 and "(+7 linhas de diferença)" in linhas[-1]

    @pytest.mark.parametrize("entrada", [None, [], "texto"])
    def test_entradas_invalidas_viram_mapa_vazio(self, entrada):
        assert self._diff(entrada, entrada) == []


class TestDiffNoLaco:
    def test_mostra_o_diff_logo_abaixo_de_tags_alteradas(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        emit = amb.de("emit")
        i = emit.index("   Tags   alteradas (1 campo)")
        assert "'0' → '1'" in emit[i + 1]  # o snapshot falso muda v: 0 -> 1

    def test_sem_alteracao_nao_mostra_diff(self, amb):
        amb.muda_texto = amb.muda_capa = False
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        _rodar(amb)
        assert not any("→" in t for t in amb.de("emit"))


class TestPreservaInfoDeCapaNoLaco:
    def test_retag_normal_pede_para_preservar_a_info_de_capa(self, amb, monkeypatch):
        vistos = []

        def tag(filename, root, final, item, album, istrack, em_image, **kw):
            vistos.append(kw)

        monkeypatch.setattr(retro_tags.metadata, "tag_flac", tag)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert vistos[0]["preserve_cover_info"] is True


# ---------------------------------------------------------------------------
# Modo --covers-only: nenhuma tag é regravada
# ---------------------------------------------------------------------------
class TestModoSoCapas:
    @pytest.fixture
    def trocas(self, amb, monkeypatch):
        registro = SimpleNamespace(chamadas=[], resultado=True)

        def trocar(arquivo, capa, cover_source=None, settings=None):
            registro.chamadas.append((os.path.basename(arquivo), cover_source))
            if registro.resultado:
                est = amb.estado.setdefault(arquivo, {"texto": 0, "capa": 0})
                est["capa"] += 1
            return registro.resultado

        monkeypatch.setattr(
            retro_tags.metadata, "replace_cover_only", trocar, raising=False
        )
        return registro

    def _rodar_so_capas(self, amb):
        return asyncio.run(
            retro_tags.retag_directory(
                str(amb.pasta),
                SimpleNamespace(),
                SimpleNamespace(multi_value_tags=True),
                covers_only=True,
            )
        )

    def _linha(self, amb):
        return _bloco(amb)[1]

    def test_troca_a_capa_sem_regravar_tags(self, amb, trocas):
        amb.arquivo("a.flac")
        stats = self._rodar_so_capas(amb)
        assert trocas.chamadas == [("a.flac", "Apple/iTunes")]
        assert amb.tags("flac") == [] and amb.tags("mp3") == []
        assert stats["apple_embedded"] == 1 and stats["updated"] == 1
        assert self._linha(amb) == "   • Alterado -- somente a capa"
        assert "Tags   não tocadas (modo só capas)" in amb.texto
        assert "SOMENTE CAPAS" in amb.texto
        assert _temporarios(amb.pasta) == []

    def test_mp3_tambem_nao_regrava_tags(self, amb, trocas):
        amb.arquivo("a.mp3")
        self._rodar_so_capas(amb)
        assert trocas.chamadas == [("a.mp3", "Apple/iTunes")]
        assert amb.tags("mp3") == []

    def test_sem_capa_nova_nada_e_gravado(self, amb, trocas):
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        stats = self._rodar_so_capas(amb)
        assert trocas.chamadas == [] and amb.chamadas == []
        assert self._linha(amb) == "   • Sem alterações"
        assert stats["unchanged"] == 1 and stats["updated"] == 0
        assert stats["apple_not_found"] == 1

    def test_candidata_nao_superior_nao_troca(self, amb, trocas):
        amb.substituir = False
        amb.arquivo("a.flac")
        stats = self._rodar_so_capas(amb)
        assert trocas.chamadas == []
        assert stats["unchanged"] == 1
        assert "mantida -- a capa atual já tem resolução igual ou maior" in amb.texto
        assert _temporarios(amb.pasta) == []

    def test_falha_ao_embutir_conta_como_falha_e_limpa_o_temporario(self, amb, trocas):
        trocas.resultado = False
        amb.arquivo("a.flac")
        stats = self._rodar_so_capas(amb)
        assert stats["apple_failed"] == 1 and stats["apple_embedded"] == 0
        assert "a gravação da imagem no arquivo falhou" in amb.texto
        assert _temporarios(amb.pasta) == []

    def test_erro_ao_trocar_conta_erro_e_segue(self, amb, monkeypatch):
        def explode(*a, **k):
            raise OSError("disco cheio")

        monkeypatch.setattr(
            retro_tags.metadata, "replace_cover_only", explode, raising=False
        )
        amb.arquivo("a.flac", track_id="1")
        stats = self._rodar_so_capas(amb)
        assert stats["errors"] == 1
        assert "erro ao gravar tags: disco cheio" in amb.texto
        assert _temporarios(amb.pasta) == []

    def test_cabecalho_do_modo_normal_continua_mostrando_multi_tags(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        assert "Multi-tags" in amb.texto and "SOMENTE CAPAS" not in amb.texto


# ---------------------------------------------------------------------------
# Capa decidida POR ÁLBUM (ID do álbum Qobuz): todas as faixas do álbum
# recebem a mesma capa; single, álbum, Deluxe e coletânea nunca se misturam.
# ---------------------------------------------------------------------------
def _decisao(url, metodo="busca do álbum"):
    return {"url": url, "source": "Apple/iTunes", "method": metodo, "note": None}


class TestCapaPorAlbum:
    def _downloads(self, amb):
        return [c[1] for c in amb.chamadas if c[0] == "download"]

    def _capas_embutidas(self, amb):
        return [c[3] for c in amb.tags("flac")]

    def test_todas_as_faixas_recebem_a_mesma_capa_baixada_uma_vez(self, amb):
        amb.album = {
            "id": "ALB",
            "title": "Disco",
            "artist": {"name": "Banda"},
            "tracks_count": 12,
        }
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        amb.apple = (None, None, "a busca por faixa não achou nada")
        for nome, tid in (("a.flac", "1"), ("b.flac", "2"), ("c.flac", "3")):
            amb.arquivo(nome, track_id=tid)
        stats = _rodar(amb)

        assert self._downloads(amb) == ["https://img/album.jpg"]  # uma vez
        capas = self._capas_embutidas(amb)
        assert len(capas) == 3 and len(set(capas)) == 1  # o mesmo arquivo
        assert stats["apple_embedded"] == 3 and stats["apple_not_found"] == 0
        assert amb.decisoes == ["ALB"]  # decidido uma vez
        assert "capa do álbum baixada" in amb.texto
        assert amb.texto.count("capa do álbum reaproveitada") == 2
        cabecalhos = [t for t in amb.de("emit") if t.startswith("== Disco")]
        assert len(cabecalhos) == 1  # uma vez por álbum, não por faixa
        assert cabecalhos[0].startswith("== Disco -- Banda  |  álbum | 12 faixas ")
        assert cabecalhos[0].endswith("=")
        assert "   Capa Apple única para todas as faixas (busca do álbum)" in amb.de(
            "emit"
        )
        assert _temporarios(amb.pasta) == []

    def test_faixa_que_a_apple_nao_achou_herda_a_capa_do_album(self, amb):
        # É o caso do VSF: sozinha não casa, mas o álbum casou.
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        amb.apple = (None, None, "versão da faixa incompatível")
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_embedded"] == 1
        assert "versão da faixa incompatível" not in amb.texto

    def test_albuns_diferentes_nao_se_misturam(self, amb):
        # Single e álbum onde a mesma faixa aparece: IDs de álbum diferentes.
        amb.album_de = {"1": {"id": "SINGLE"}, "2": {"id": "ALBUM"}}
        amb.decisao_de = {
            "SINGLE": _decisao("https://img/single.jpg"),
            "ALBUM": _decisao("https://img/album.jpg"),
        }
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        _rodar(amb)
        assert amb.decisoes == ["SINGLE", "ALBUM"]
        assert self._downloads(amb) == [
            "https://img/single.jpg",
            "https://img/album.jpg",
        ]

    def test_voltar_a_um_album_visto_rebaixa_a_capa_sem_redecidir(self, amb):
        # Só a capa do álbum ATUAL fica em disco; a decisão é lembrada.
        amb.album_de = {"1": {"id": "A1"}, "2": {"id": "A2"}, "3": {"id": "A1"}}
        amb.decisao_de = {
            "A1": _decisao("https://img/1.jpg"),
            "A2": _decisao("https://img/2.jpg"),
        }
        for nome, tid in (("a.flac", "1"), ("b.flac", "2"), ("c.flac", "3")):
            amb.arquivo(nome, track_id=tid)
        _rodar(amb)
        assert amb.decisoes == ["A1", "A2"]
        assert self._downloads(amb) == [
            "https://img/1.jpg",
            "https://img/2.jpg",
            "https://img/1.jpg",
        ]
        assert _temporarios(amb.pasta) == []

    def test_sem_decisao_por_album_cada_faixa_e_avaliada_sozinha(self, amb):
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        stats = _rodar(amb)
        assert self._downloads(amb) == ["https://img/a.jpg", "https://img/a.jpg"]
        assert stats["apple_embedded"] == 2
        assert "Capa Apple única" not in amb.texto

    def test_capa_nao_superior_em_uma_faixa_nao_apaga_a_do_album(
        self, amb, monkeypatch
    ):
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        # 1ª faixa já tem capa igual/maior; a 2ª não tem capa melhor.
        monkeypatch.setattr(
            retro_tags.metadata,
            "should_replace_embedded_cover",
            lambda arquivo, candidata: os.path.basename(arquivo) != "a.flac",
        )
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        _rodar(amb)
        a, b = amb.tags("flac")
        assert a[2] is False and a[3] is None  # preservada
        assert b[2] is True and b[3]  # a capa do álbum ainda existia p/ a 2ª
        assert self._downloads(amb) == ["https://img/album.jpg"]
        assert _temporarios(amb.pasta) == []

    def test_download_do_album_falha_conta_por_faixa_e_nada_e_embutido(self, amb):
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        amb.baixar_bytes = None
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        stats = _rodar(amb)
        assert stats["apple_failed"] == 2 and stats["apple_embedded"] == 0
        assert all(c[2] is False for c in amb.tags("flac"))
        assert "download falhou" in amb.texto

    def test_erro_ao_gravar_nao_deixa_temporario_da_capa_do_album(self, amb):
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        amb.erro_ao_gravar = OSError("disco cheio")
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        stats = _rodar(amb)
        assert stats["errors"] == 2
        assert _temporarios(amb.pasta) == []

    def test_so_capas_usa_a_mesma_capa_do_album_e_nao_regrava_tags(
        self, amb, monkeypatch
    ):
        amb.decisao_de["ALB"] = _decisao("https://img/album.jpg")
        trocas = []

        def trocar(arquivo, capa, cover_source=None, settings=None):
            trocas.append((os.path.basename(arquivo), capa))
            return True

        monkeypatch.setattr(
            retro_tags.metadata, "replace_cover_only", trocar, raising=False
        )
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        asyncio.run(
            retro_tags.retag_directory(
                str(amb.pasta),
                SimpleNamespace(),
                SimpleNamespace(multi_value_tags=True),
                covers_only=True,
            )
        )
        assert [t[0] for t in trocas] == ["a.flac", "b.flac"]
        assert trocas[0][1] == trocas[1][1]
        assert amb.tags("flac") == []
        assert self._downloads(amb) == ["https://img/album.jpg"]
        assert _temporarios(amb.pasta) == []


# ---------------------------------------------------------------------------
# _decide_album_cover (a decisão em si: nível do álbum, depois consenso)
# ---------------------------------------------------------------------------
def _album_qobuz(n_faixas=5, **extra):
    album = {
        "id": "Q1",
        "title": "Anti-Herói",
        "upc": "0602",
        "artist": {"name": "Jão"},
        "tracks_count": n_faixas,
        "tracks": {
            "items": [{"id": i, "title": f"Faixa {i}"} for i in range(n_faixas)]
        },
    }
    album.update(extra)
    return album


@pytest.fixture
def decisao_env(monkeypatch):
    """Substitui a busca do álbum e a busca por faixa; guarda as chamadas."""
    env = SimpleNamespace(
        album=("URL-ALBUM", "Apple/iTunes", None),
        faixa={},  # id da faixa -> (url, fonte, motivo)
        padrao_faixa=(None, None, "sem candidato"),
        args_album=None,
        faixas_pesquisadas=[],
    )

    async def get_album(**kw):
        env.args_album = kw
        if isinstance(env.album, Exception):
            raise env.album
        return env.album

    async def por_faixa(item, album, sessao):
        env.faixas_pesquisadas.append(item["id"])
        res = env.faixa.get(item["id"], env.padrao_faixa)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr("qobuz_dl.utils.get_apple_hq_cover", get_album)
    monkeypatch.setattr(retro_tags, "_fetch_apple_cover_for_track", por_faixa)
    return env


class TestDecideAlbumCover:
    async def test_busca_no_nivel_do_album_vence_e_nao_pesquisa_faixas(
        self, decisao_env
    ):
        resultados = {}
        d = await retro_tags._decide_album_cover(_album_qobuz(), object(), resultados)
        assert d["url"] == "URL-ALBUM" and d["method"] == "busca do álbum"
        assert decisao_env.faixas_pesquisadas == [] and resultados == {}
        a = decisao_env.args_album
        assert (a["upc"], a["artist"], a["album"]) == ("0602", "Jão", "Anti-Herói")
        assert "track_title" not in a  # busca no nível do ÁLBUM
        assert a["deep_album_search"] is True and a["release_kind"] == "full"

    async def test_album_com_version_envia_o_titulo_completo(self, decisao_env):
        await retro_tags._decide_album_cover(
            _album_qobuz(title="LOBOS", version="Deluxe"), object(), {}
        )
        assert decisao_env.args_album["album"] == "LOBOS (Deluxe)"

    async def test_single_e_pedido_como_single(self, decisao_env):
        await retro_tags._decide_album_cover(_album_qobuz(1), object(), {})
        assert decisao_env.args_album["release_kind"] == "single"

    async def test_consenso_das_faixas_quando_o_album_nao_e_achado(self, decisao_env):
        decisao_env.album = (None, None, "nenhum álbum compatível")
        for i in range(4):
            decisao_env.faixa[i] = ("URL-CONSENSO", "Apple/iTunes", None)
        resultados = {}
        d = await retro_tags._decide_album_cover(_album_qobuz(5), object(), resultados)
        assert d["url"] == "URL-CONSENSO"
        assert d["method"] == "consenso de 4/5 faixas"
        assert sorted(resultados) == ["0", "1", "2", "3", "4"]  # reaproveitáveis

    async def test_maioria_vence_a_capa_com_menos_votos(self, decisao_env):
        decisao_env.album = (None, None, "x")
        for i in range(3):
            decisao_env.faixa[i] = ("MAIORIA", "Apple/iTunes", None)
        decisao_env.faixa[3] = ("OUTRA", "Apple/iTunes", None)
        d = await retro_tags._decide_album_cover(_album_qobuz(5), object(), {})
        assert d["url"] == "MAIORIA"

    async def test_empate_nao_decide(self, decisao_env):
        decisao_env.album = (None, None, "motivo-do-album")
        decisao_env.faixa[0] = ("A", "Apple/iTunes", None)
        decisao_env.faixa[1] = ("A", "Apple/iTunes", None)
        decisao_env.faixa[2] = ("B", "Apple/iTunes", None)
        decisao_env.faixa[3] = ("B", "Apple/iTunes", None)
        d = await retro_tags._decide_album_cover(_album_qobuz(5), object(), {})
        assert d["url"] is None and d["note"] == "motivo-do-album"

    async def test_um_voto_so_nao_basta(self, decisao_env):
        decisao_env.album = (None, None, "x")
        decisao_env.faixa[0] = ("A", "Apple/iTunes", None)
        d = await retro_tags._decide_album_cover(_album_qobuz(5), object(), {})
        assert d["url"] is None

    async def test_coletanea_com_capas_espalhadas_nao_decide(self, decisao_env):
        decisao_env.album = (None, None, "x")
        for i in range(5):
            decisao_env.faixa[i] = (f"CAPA-{i}", "Apple/iTunes", None)
        d = await retro_tags._decide_album_cover(_album_qobuz(5), object(), {})
        assert d["url"] is None

    async def test_album_de_uma_faixa_nao_faz_votacao(self, decisao_env):
        decisao_env.album = (None, None, "x")
        d = await retro_tags._decide_album_cover(_album_qobuz(1), object(), {})
        assert d["url"] is None and decisao_env.faixas_pesquisadas == []

    async def test_erro_na_busca_do_album_cai_na_votacao(self, decisao_env):
        decisao_env.album = RuntimeError("rede")
        for i in range(3):
            decisao_env.faixa[i] = ("URL", "Apple/iTunes", None)
        d = await retro_tags._decide_album_cover(_album_qobuz(4), object(), {})
        assert d["url"] == "URL"

    async def test_erro_em_uma_faixa_nao_derruba_a_votacao(self, decisao_env):
        decisao_env.album = (None, None, "x")
        decisao_env.faixa[0] = RuntimeError("boom")
        decisao_env.faixa[1] = ("URL", "Apple/iTunes", None)
        decisao_env.faixa[2] = ("URL", "Apple/iTunes", None)
        resultados = {}
        d = await retro_tags._decide_album_cover(_album_qobuz(4), object(), resultados)
        assert d["url"] == "URL"
        assert resultados["0"][0] is None and "boom" in resultados["0"][2]


class TestTipoDeLancamento:
    @pytest.mark.parametrize(
        "album, esperado",
        [
            ({"tracks_count": 1}, "single"),
            ({"tracks_count": 3}, "single"),
            ({"tracks_count": 5}, "full"),  # EP
            ({"tracks_count": 12}, "full"),  # álbum
            ({"title": "Turnê (Live)", "tracks_count": 2}, "full"),  # ao vivo
            ({"title": "Greatest Hits", "tracks_count": 2}, "full"),  # coletânea
            ({}, None),
            (None, None),
        ],
    )
    def test_release_kind(self, album, esperado):
        assert retro_tags._release_kind(album) == esperado

    def test_faixas_da_lista_contam_quando_falta_tracks_count(self):
        album = {"tracks": {"items": [{"id": 1}, {"id": 2}]}}
        assert retro_tags._release_kind(album) == "single"

    def test_pedido_por_faixa_leva_o_tipo(self, monkeypatch):
        vistos = {}

        async def falsa(**kw):
            vistos.update(kw)
            return ("U", "Apple/iTunes", None)

        monkeypatch.setattr("qobuz_dl.utils.get_apple_hq_cover", falsa)
        item = {"title": "Ressaca", "performer": {"name": "Jão"}}
        asyncio.run(
            retro_tags._fetch_apple_cover_for_track(
                item, {"title": "Ressaca", "tracks_count": 1}, object()
            )
        )
        assert vistos["release_kind"] == "single"


# ---------------------------------------------------------------------------
# Cabeçalho do álbum, legenda, espaçamento e resumo
# ---------------------------------------------------------------------------
def _album_com_titulo(id_, titulo, faixas=12):
    return {
        "id": id_,
        "title": titulo,
        "artist": {"name": "Banda"},
        "tracks_count": faixas,
    }


class TestCabecalhoDeAlbum:
    def _cabecalhos(self, amb):
        return [t for t in amb.de("emit") if t.startswith("== ")]

    def test_uma_vez_por_album_e_de_novo_ao_voltar(self, amb):
        amb.album_de = {
            "1": _album_com_titulo("A1", "Primeiro"),
            "2": _album_com_titulo("A2", "Segundo"),
            "3": _album_com_titulo("A1", "Primeiro"),
        }
        for nome, tid in (("a.flac", "1"), ("b.flac", "2"), ("c.flac", "3")):
            amb.arquivo(nome, track_id=tid)
        _rodar(amb)
        titulos = [c.split(" -- ")[0] for c in self._cabecalhos(amb)]
        assert titulos == ["== Primeiro", "== Segundo", "== Primeiro"]

    def test_faixas_do_mesmo_album_nao_repetem_o_cabecalho(self, amb):
        amb.album = _album_com_titulo("ALB", "Disco")
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        _rodar(amb)
        assert len(self._cabecalhos(amb)) == 1

    def test_tipo_do_lancamento_aparece_no_cabecalho(self, amb):
        amb.album = _album_com_titulo("ALB", "Ressaca", faixas=1)
        amb.arquivo("a.flac")
        _rodar(amb)
        assert "|  single | 1 faixa " in self._cabecalhos(amb)[0]

    def test_sem_decisao_por_album_o_cabecalho_explica(self, amb):
        amb.album = _album_com_titulo("ALB", "Disco")
        amb.arquivo("a.flac")
        _rodar(amb)
        assert (
            "   Sem capa única para o álbum: cada faixa é avaliada separadamente"
            in amb.de("emit")
        )

    def test_album_sem_titulo_nao_imprime_cabecalho(self, amb):
        amb.arquivo("a.flac")  # o álbum do ambiente é só {"id": "ALB"}
        _rodar(amb)
        assert self._cabecalhos(amb) == []

    def test_cabecalho_vem_antes_do_bloco_da_faixa_com_linha_em_branco(self, amb):
        amb.album = _album_com_titulo("ALB", "Disco")
        amb.arquivo("a.flac")
        _rodar(amb)
        emit = amb.de("emit")
        i = next(i for i, t in enumerate(emit) if t.startswith("== Disco"))
        assert emit[i - 1] == ""
        j = next(j for j, t in enumerate(emit) if t.startswith("[1/1]"))
        assert j > i and emit[j - 1] == ""


class TestLegendaEEspacamento:
    def test_legenda_das_cores_no_inicio(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        emit = amb.de("emit")
        i = next(i for i, t in enumerate(emit) if "Legenda das cores" in t)
        legenda = "\n".join(emit[i : i + 5])
        for palavra in ("verde", "amarelo", "cinza", "vermelho"):
            assert palavra in legenda
        assert i < next(j for j, t in enumerate(emit) if t.startswith("[1/1]"))

    def test_linha_em_branco_antes_de_cada_faixa(self, amb):
        for nome, tid in (("a.flac", "1"), ("b.flac", "2"), ("c.flac", "3")):
            amb.arquivo(nome, track_id=tid)
        _rodar(amb)
        emit = amb.de("emit")
        for numero in (1, 2, 3):
            i = next(i for i, t in enumerate(emit) if t.startswith(f"[{numero}/3]"))
            assert emit[i - 1] == ""

    def test_mensagens_de_pulo_tambem_ficam_separadas(self, amb):
        amb.arquivo("a.flac", track_id=None)
        _rodar(amb)
        i = next(
            i for i, (_, t) in enumerate(amb.linhas) if "sem ID Qobuz nas tags" in t
        )
        assert amb.linhas[i - 1] == ("emit", "")

    def test_sem_travessao_nem_simbolos_exoticos_no_bloco(self, amb):
        # a-Shell: só ASCII + os símbolos que o terminal já exibia.
        amb.album = _album_com_titulo("ALB", "Disco")
        amb.arquivo("a.flac")
        _rodar(amb)
        # Escapes unicode: travessão, ponto médio, traço de caixa e círculo.
        for caractere in ("\u2014", "\u00b7", "\u2501", "\u25cf"):
            assert caractere not in amb.texto


class TestCoresDoLog:
    """Verde = alterou; amarelo = atenção; cinza = nada a alterar."""

    @pytest.fixture
    def cores(self, monkeypatch):
        for nome, marca in (
            ("GREEN", "<G>"),
            ("YELLOW", "<Y>"),
            ("MUTED", "<M>"),
            ("RED", "<R>"),
            ("CYAN", "<C>"),
            ("OFF", "</>"),
            ("RESET", "</>"),
        ):
            monkeypatch.setattr(retro_tags, nome, marca)

    def _bloco_cor(self, amb, cor):
        return _bloco(amb, prefixo=f"{cor}[1/1]")

    def test_alterado_fica_verde(self, amb, cores):
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = self._bloco_cor(amb, "<G>")
        assert bloco[1] == "   <G>• Alterado -- tags e capa</>"
        assert bloco[2] == "<G>   Tags   alteradas (1 campo)</>"
        assert "<G>   Capa   substituída pela capa da Apple/iTunes</>" in bloco

    def test_nada_a_alterar_fica_cinza(self, amb, cores):
        amb.muda_texto = amb.muda_capa = False
        amb.substituir = False
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = self._bloco_cor(amb, "<M>")
        assert bloco[1] == "   <M>• Sem alterações</>"
        assert bloco[2] == "<M>   Tags   já estavam corretas</>"
        assert bloco[3].startswith("<M>   Capa   mantida -- ")

    def test_pendencia_na_capa_fica_amarela(self, amb, cores):
        amb.apple = (None, None, "motivo-xyz")
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = self._bloco_cor(amb, "<Y>")
        assert bloco[1].startswith("   <Y>• Alterado (somente as tags)")
        assert bloco[2].startswith("<G>   Tags")  # a tag alterada segue verde
        assert any(t.startswith("<Y>   Capa   não encontrada na Apple") for t in bloco)
        assert "<M>          Motivo: motivo-xyz</>" in bloco

    def test_sem_alteracao_mas_capa_nao_achada_destaca_so_a_capa(self, amb, cores):
        amb.muda_texto = amb.muda_capa = False
        amb.apple = (None, None, "motivo-xyz")
        amb.arquivo("a.flac")
        _rodar(amb)
        bloco = self._bloco_cor(amb, "<M>")  # o arquivo em si: nada alterado
        assert bloco[1] == "   <M>• Sem alterações</>"
        assert any(t.startswith("<Y>   Capa   não encontrada") for t in bloco)

    def test_erro_na_etapa_da_apple_fica_amarelo(self, amb, cores):
        amb.apple = RuntimeError("quebrou")
        amb.arquivo("a.flac")
        _rodar(amb)
        assert any(
            t.startswith("<Y>   Capa   erro na etapa da Apple") for t in amb.de("emit")
        )

    def test_diff_verde_para_novo_e_alterado_amarelo_para_removido(self, cores):
        linhas = retro_tags.format_tag_diff(
            {"VELHA": ["a"], "MUDA": ["x"]}, {"NOVA": ["b"], "MUDA": ["y"]}
        )
        texto = "\n".join(linhas)
        assert "<G>+ NOVA:</>" in texto
        assert "<G>~ MUDA:</>" in texto
        assert "<Y>- VELHA:</>" in texto

    def test_modo_so_capas_tags_ficam_cinza(self, amb, cores, monkeypatch):
        monkeypatch.setattr(
            retro_tags.metadata,
            "replace_cover_only",
            lambda *a, **k: True,
            raising=False,
        )
        amb.arquivo("a.flac")
        asyncio.run(
            retro_tags.retag_directory(
                str(amb.pasta),
                SimpleNamespace(),
                SimpleNamespace(multi_value_tags=True),
                covers_only=True,
            )
        )
        assert "<M>   Tags   não tocadas (modo só capas)</>" in amb.de("emit")


class TestResumoFinal:
    def _linhas(self, amb):
        emit = amb.de("emit")
        i = next(i for i, t in enumerate(emit) if "Correção de tags concluída" in t)
        return emit[i:]

    def test_cada_numero_vem_com_uma_explicacao(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        resumo = "\n".join(self._linhas(amb))
        assert "Resumo:" in resumo
        assert "Arquivos analisados       : 1  faixas .flac/.mp3 encontradas" in resumo
        assert "Atualizados               : 1  faixas com tags e/ou capa" in resumo
        assert "Capas Apple embutidas     : 1  faixas que receberam a capa" in resumo
        assert "Erros                     : 0  falhas ao consultar" in resumo

    def test_linha_em_branco_antes_do_resumo(self, amb):
        amb.arquivo("a.flac")
        _rodar(amb)
        emit = amb.de("emit")
        i = next(i for i, t in enumerate(emit) if "Correção de tags concluída" in t)
        assert emit[i - 1] == "" and emit[i + 1] == ""

    def test_cores_dos_numeros(self, amb, monkeypatch):
        for nome, marca in (
            ("GREEN", "<G>"),
            ("YELLOW", "<Y>"),
            ("MUTED", "<M>"),
            ("RED", "<R>"),
            ("CYAN", "<C>"),
            ("OFF", "</>"),
            ("RESET", "</>"),
        ):
            monkeypatch.setattr(retro_tags, nome, marca)
        amb.apple = (None, None, "x")
        amb.arquivo("a.flac")
        amb.arquivo("b.flac", track_id=None)
        _rodar(amb)
        resumo = "\n".join(self._linhas(amb))
        assert "<G>1</>" in resumo  # Atualizados > 0
        assert "<Y>1</>" in resumo  # sem ID Qobuz / capa não encontrada > 0
        assert "Erros                     :</> <M>0</>" in resumo  # zero = cinza


class TestDecideAlbumCoverVersaoCerta:
    """A versão certa do álbum é confirmada pela lista de faixas da Qobuz."""

    async def test_envia_a_lista_de_faixas_com_a_versao_e_as_lojas_extras(
        self, decisao_env
    ):
        album = _album_qobuz(2)
        album["tracks"]["items"][0].update(
            title="Take My Breath", version="Single Version"
        )
        album["tracks"]["items"][1].update(title="Outra", version="")
        await retro_tags._decide_album_cover(album, object(), {})
        a = decisao_env.args_album
        assert a["album_tracks"] == ["Take My Breath (Single Version)", "Outra"]
        assert a["fallback_countries"] is True

    async def test_album_sem_lista_de_faixas_nao_envia_album_tracks(self, decisao_env):
        album = _album_qobuz(0)
        await retro_tags._decide_album_cover(album, object(), {})
        assert decisao_env.args_album["album_tracks"] is None

    async def test_trace_so_e_repassado_quando_pedido(self, decisao_env):
        await retro_tags._decide_album_cover(_album_qobuz(2), object(), {})
        assert "trace" not in decisao_env.args_album
        trace = []
        await retro_tags._decide_album_cover(_album_qobuz(2), object(), {}, trace=trace)
        assert decisao_env.args_album["trace"] is trace


class TestTituloDaFaixaQobuz:
    @pytest.mark.parametrize(
        "item, esperado",
        [
            (
                {"title": "Take My Breath", "version": "Single Version"},
                "Take My Breath (Single Version)",
            ),
            (
                {"title": "Fat Juicy &…", "version": "Radio Edit"},
                "Fat Juicy &… (Radio Edit)",
            ),
            (
                {"title": "Musica (Radio Edit)", "version": "Radio Edit"},
                "Musica (Radio Edit)",
            ),
            ({"title": "Simples"}, "Simples"),
            ({"title": " Espaço ", "version": " "}, "Espaço"),
            ({}, ""),
        ],
    )
    def test_titulo_com_versao(self, item, esperado):
        assert retro_tags._qobuz_track_title(item) == esperado


# ---------------------------------------------------------------------------
# Modo --tags-only: a Apple não é consultada e a capa fica como está
# ---------------------------------------------------------------------------
class TestModoSoTags:
    @pytest.fixture
    def sem_apple(self, amb, monkeypatch):
        consultas = []

        async def proibido(*args, **kwargs):
            consultas.append(args)
            raise AssertionError("a Apple não pode ser consultada no modo só tags")

        monkeypatch.setattr(retro_tags, "_fetch_apple_cover_for_track", proibido)
        monkeypatch.setattr(retro_tags, "_decide_album_cover", proibido)
        return consultas

    def _rodar_so_tags(self, amb):
        return asyncio.run(
            retro_tags.retag_directory(
                str(amb.pasta),
                SimpleNamespace(),
                SimpleNamespace(multi_value_tags=True),
                tags_only=True,
            )
        )

    def test_grava_as_tags_sem_consultar_nem_baixar_capa(self, amb, sem_apple):
        amb.arquivo("a.flac")
        stats = self._rodar_so_tags(amb)
        assert sem_apple == []
        assert ("download", "u") not in amb.chamadas
        assert [c for c in amb.chamadas if c[0] == "download"] == []
        # tag_flac chamado SEM capa nova (a embutida é preservada)
        assert amb.tags("flac") == [("flac", "a.flac", False, None, None)]
        assert stats["updated"] == 1
        assert stats["apple_embedded"] == 0
        assert stats["apple_not_found"] == 0 and stats["apple_failed"] == 0
        assert _temporarios(amb.pasta) == []

    def test_mp3_tambem(self, amb, sem_apple):
        amb.arquivo("a.mp3")
        self._rodar_so_tags(amb)
        assert amb.tags("mp3") == [("mp3", "a.mp3", False, None, None)]

    def test_log_explica_o_modo_e_nao_fala_de_apple_no_resumo(self, amb, sem_apple):
        amb.arquivo("a.flac")
        self._rodar_so_tags(amb)
        assert "SOMENTE TAGS" in amb.texto
        assert "SOMENTE CAPAS" not in amb.texto
        assert "Multi-tags" in amb.texto
        assert "Capa   não tocada (modo só tags)" in amb.texto
        assert "Capas Apple embutidas" not in amb.texto
        assert "Capas Apple não encontradas" not in amb.texto

    def test_nao_diz_que_a_capa_nao_foi_achada(self, amb, sem_apple):
        amb.apple = (None, None, "sem candidato")
        amb.arquivo("a.flac")
        self._rodar_so_tags(amb)
        assert "não encontrada na Apple" not in amb.texto
        assert "sem candidato" not in amb.texto

    def test_tags_ja_corretas_contam_como_sem_alteracao(self, amb, sem_apple):
        amb.muda_texto = False
        amb.arquivo("a.flac")
        stats = self._rodar_so_tags(amb)
        assert stats["unchanged"] == 1 and stats["updated"] == 0

    def test_varios_arquivos_nao_consultam_a_apple(self, amb, sem_apple):
        amb.arquivo("a.flac", track_id="1")
        amb.arquivo("b.flac", track_id="2")
        stats = self._rodar_so_tags(amb)
        assert sem_apple == [] and stats["updated"] == 2

    def test_modo_normal_continua_consultando_a_apple(self, amb):
        amb.arquivo("a.flac")
        stats = _rodar(amb)
        assert stats["apple_embedded"] == 1
        assert "SOMENTE TAGS" not in amb.texto

    def test_modos_excludentes(self, amb):
        with pytest.raises(ValueError):
            asyncio.run(
                retro_tags.retag_directory(
                    str(amb.pasta),
                    SimpleNamespace(),
                    SimpleNamespace(multi_value_tags=True),
                    covers_only=True,
                    tags_only=True,
                )
            )

    @pytest.mark.parametrize(
        "covers, tags, esperado",
        [
            (False, False, "both"),
            (True, False, "covers"),
            (False, True, "tags"),
        ],
    )
    def test_resolve_mode(self, covers, tags, esperado):
        assert retro_tags.resolve_mode(covers, tags) == esperado
