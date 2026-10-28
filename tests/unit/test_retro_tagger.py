"""Testa qobuz_dl/retro_tagger.py -- as três funções que dá pra cobrir com
confiança: `extract_track_id`, `inspect_existing_lyrics` (leitura de tag,
via dublês do mutagen -- mesmo padrão de test_sync_fingerprint.py) e
`fetch_qobuz_lyrics_raw` (rede, via um client falso que nunca chama a
assinatura de verdade).

`process_retroactive_lyrics_async` (585 linhas, orquestra leitura de
biblioteca inteira + rede + escrita de tag) fica de fora -- mesma
categoria de `_tui_select`: orquestração grande demais pra cobrir com
confiança numa passada só, precisa virar um trabalho próprio.

IMPORTANTE: todo texto usado como "letra" nos testes abaixo é placeholder
fabricado pra este teste (frases genéricas tipo "Linha de teste") -- não é
letra de nenhuma música real. O que está sendo testado é a lógica de
parsing/deteção (existe letra? em que campo? é bilíngue?), não o conteúdo
em si.
"""

import collections
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qobuz_dl import retro_tagger
from qobuz_dl.retro_tagger import (
    extract_track_id,
    fetch_qobuz_lyrics_raw,
    inspect_existing_lyrics,
)

_PLACEHOLDER = "Linha de teste um\nLinha de teste dois"


class _FakeFLAC(dict):
    def get(self, chave, default=None):
        return dict.get(self, chave, default)


class _FakeTXXXFrame:
    def __init__(self, desc, texto):
        self.desc = desc
        self.text = [texto]


class _FakeUSLTFrame:
    def __init__(self, texto):
        self.text = texto


class _FakeID3:
    def __init__(self, txxx_frames=None, comm_frames=None, uslt_frames=None):
        self._txxx = txxx_frames or []
        self._comm = comm_frames or []
        self._uslt = uslt_frames or []

    def getall(self, tipo):
        if tipo == "TXXX":
            return self._txxx
        if tipo == "COMM":
            return self._comm
        if tipo == "USLT":
            return self._uslt
        return []


# --------------------------------------------------------------------
# extract_track_id
# --------------------------------------------------------------------
class TestExtractTrackIdFlac:
    def test_tag_qobuztrackid_direta(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(QOBUZTRACKID=["abc123"])
        )
        assert extract_track_id("faixa.flac") == "abc123"

    def test_tag_alternativa_com_espaco(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(**{"QOBUZ TRACK ID": ["xyz789"]}),
        )
        assert extract_track_id("faixa.flac") == "xyz789"

    def test_id_no_comentario_via_regex(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(COMMENT=["Ripado em 2024 - Trk ID: 998877"]),
        )
        assert extract_track_id("faixa.flac") == "998877"

    def test_sem_nenhum_id_devolve_none(self, monkeypatch):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        assert extract_track_id("faixa.flac") is None

    def test_valor_com_espacos_e_stripado(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(QOBUZTRACKID=["  ab12  "])
        )
        assert extract_track_id("faixa.flac") == "ab12"

    def test_excecao_na_leitura_nao_lanca(self, monkeypatch):
        def _explode(path):
            raise OSError("arquivo corrompido")

        monkeypatch.setattr(retro_tagger, "FLAC", _explode)
        assert extract_track_id("faixa.flac") is None


class TestExtractTrackIdMp3:
    def test_txxx_com_desc_exata(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "id3",
            SimpleNamespace(
                ID3=lambda path: _FakeID3(
                    txxx_frames=[_FakeTXXXFrame("QOBUZTRACKID", "id-456")]
                )
            ),
        )
        assert extract_track_id("faixa.mp3") == "id-456"

    def test_txxx_com_desc_normalizada_espaco_e_underscore(self, monkeypatch):
        # "QOBUZ TRACK ID" e "QOBUZ_TRACK_ID" têm que casar com o mesmo
        # comparador normalizado (maiúsculo, sem espaço/underscore).
        monkeypatch.setattr(
            retro_tagger,
            "id3",
            SimpleNamespace(
                ID3=lambda path: _FakeID3(
                    txxx_frames=[_FakeTXXXFrame("qobuz_track_id", "id-789")]
                )
            ),
        )
        assert extract_track_id("faixa.mp3") == "id-789"

    def test_id_no_comm_via_regex(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "id3",
            SimpleNamespace(
                ID3=lambda path: _FakeID3(
                    comm_frames=[SimpleNamespace(text=["nota: Trk ID: 55443"])]
                )
            ),
        )
        assert extract_track_id("faixa.mp3") == "55443"

    def test_sem_nenhum_id_devolve_none(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger, "id3", SimpleNamespace(ID3=lambda path: _FakeID3())
        )
        assert extract_track_id("faixa.mp3") is None


class TestExtractTrackIdExtensaoDesconhecida:
    def test_extensao_nao_flac_nem_mp3_devolve_none_direto(self):
        # Nem entra no if nem no elif -- nenhum mock precisa ser
        # registrado, é o teste de que a função não tenta ler o arquivo.
        assert extract_track_id("faixa.m4a") is None


# --------------------------------------------------------------------
# inspect_existing_lyrics
# --------------------------------------------------------------------
class TestInspectExistingLyricsFlac:
    def test_letra_embutida_em_lyrics(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER])
        )
        resultado = inspect_existing_lyrics("faixa.flac")
        assert resultado["has_lyrics"] is True
        assert resultado["content"] == _PLACEHOLDER

    def test_fallback_para_unsyncedlyrics_quando_lyrics_ausente(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(UNSYNCEDLYRICS=[_PLACEHOLDER]),
        )
        resultado = inspect_existing_lyrics("faixa.flac")
        assert resultado["has_lyrics"] is True
        assert resultado["content"] == _PLACEHOLDER

    def test_idioma_embutido_e_lido_em_minusculo(self, monkeypatch):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER], LYRICS_LANG=["PT-BR"]),
        )
        resultado = inspect_existing_lyrics("faixa.flac")
        assert resultado["language"] == "pt-br"

    def test_sem_letra_nenhuma(self, monkeypatch, tmp_path):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = str(tmp_path / "faixa.flac")
        resultado = inspect_existing_lyrics(caminho)
        assert resultado == {
            "has_lyrics": False,
            "is_bilingual": False,
            "is_synced": False,
            "source": None,
            "content": "",
            "lrc_exists": False,
            "language": None,
        }


class TestInspectExistingLyricsIsSynced:
    """`is_synced` olha o CONTEÚDO da letra, não a existência de um .lrc."""

    _SYNC = "[00:01.00]Linha de teste um\n[00:05.00]Linha de teste dois"

    def test_embutida_com_marcas_de_tempo_e_sem_lrc_ao_lado(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[self._SYNC])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_synced"] is True
        assert resultado["lrc_exists"] is False

    def test_embutida_em_texto_simples(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_synced"] is False

    def test_lrc_ao_lado_nao_torna_sincronizada_uma_letra_embutida_simples(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER])
        )
        caminho = tmp_path / "faixa.flac"
        caminho.write_bytes(b"")
        (tmp_path / "faixa.lrc").write_text(self._SYNC, encoding="utf-8")
        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["lrc_exists"] is True
        assert resultado["is_synced"] is False

    def test_so_arquivo_lrc_com_marcas_de_tempo(self, monkeypatch, tmp_path):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "faixa.flac"
        caminho.write_bytes(b"")
        (tmp_path / "faixa.lrc").write_text(self._SYNC, encoding="utf-8")
        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["is_synced"] is True

    def test_arquivo_txt_de_letra_simples(self, monkeypatch, tmp_path):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "faixa.flac"
        caminho.write_bytes(b"")
        (tmp_path / "faixa.txt").write_text(_PLACEHOLDER, encoding="utf-8")
        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["has_lyrics"] is True
        assert resultado["is_synced"] is False


class TestInspectExistingLyricsOrigem:
    """`source`: de onde veio a letra atual (campo próprio, comentário ou .lrc)."""

    _SYNC = "[00:01.00]Linha de teste um\n[00:05.00]Linha de teste dois"

    def test_flac_le_lyrics_source(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(LYRICS=[self._SYNC], LYRICS_SOURCE=["Musixmatch"]),
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["source"] == "Musixmatch"

    def test_flac_sem_campo_usa_a_linha_letra_do_comentario(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(
                LYRICS=[self._SYNC],
                COMMENT=["Fonte: Qobuz\nLetra: BiniLyrics (sincronizada, EN)"],
            ),
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["source"] == "BiniLyrics"

    def test_arquivo_antigo_sem_origem_registrada(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[self._SYNC])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["source"] is None

    def test_sem_letra_nao_tem_origem(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS_SOURCE=["Qobuz"])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["has_lyrics"] is False
        assert resultado["source"] is None

    def test_letra_so_no_lrc_usa_o_by_do_cabecalho(self, monkeypatch, tmp_path):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "faixa.flac"
        caminho.write_bytes(b"")
        (tmp_path / "faixa.lrc").write_text(
            "[by:LRCLIB]\n" + self._SYNC, encoding="utf-8"
        )
        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["source"] == "LRCLIB"


class TestInspectExistingLyricsArquivosExternos:
    def test_le_arquivo_lrc_quando_nao_ha_letra_embutida(self, monkeypatch, tmp_path):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "faixa.flac"
        caminho.write_bytes(b"")  # o FLAC() é mockado, só precisa existir p/ os.path
        lrc = tmp_path / "faixa.lrc"
        lrc.write_text(f"[la: en]\n{_PLACEHOLDER}", encoding="utf-8")

        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["has_lyrics"] is True
        assert resultado["lrc_exists"] is True
        assert resultado["language"] == "en"

    def test_le_arquivo_txt_quando_nao_ha_lrc_nem_letra_embutida(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "faixa.flac"
        txt = tmp_path / "faixa.txt"
        txt.write_text(_PLACEHOLDER, encoding="utf-8")

        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["has_lyrics"] is True
        assert resultado["content"] == _PLACEHOLDER

    def test_txt_com_tracklist_no_caminho_e_ignorado(self, monkeypatch, tmp_path):
        # Evita confundir um "Tracklist.txt" (lista de faixas do álbum,
        # às vezes salvo do lado dos arquivos) com um arquivo de letra.
        monkeypatch.setattr(retro_tagger, "FLAC", lambda path: _FakeFLAC())
        caminho = tmp_path / "Tracklist.flac"
        txt = tmp_path / "Tracklist.txt"
        txt.write_text(_PLACEHOLDER, encoding="utf-8")

        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["has_lyrics"] is False

    def test_letra_embutida_tem_prioridade_sobre_arquivo_externo(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=["Versão embutida"])
        )
        caminho = tmp_path / "faixa.flac"
        txt = tmp_path / "faixa.txt"
        txt.write_text("Versão do arquivo externo", encoding="utf-8")

        resultado = inspect_existing_lyrics(str(caminho))
        assert resultado["content"] == "Versão embutida"


class TestInspectExistingLyricsBilingue:
    def test_separador_de_traducao_detecta_bilingue(self, monkeypatch, tmp_path):
        conteudo = "Linha original » Linha traduzida"
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[conteudo])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_bilingual"] is True

    def test_marcador_traducao_detecta_bilingue(self, monkeypatch, tmp_path):
        conteudo = f"{_PLACEHOLDER}\n--- TRADUÇÃO ---\nOutra linha"
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[conteudo])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_bilingual"] is True

    def test_idioma_composto_detecta_bilingue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger,
            "FLAC",
            lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER], LYRICS_LANG=["en+pt"]),
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_bilingual"] is True

    def test_letra_normal_nao_e_bilingue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            retro_tagger, "FLAC", lambda path: _FakeFLAC(LYRICS=[_PLACEHOLDER])
        )
        resultado = inspect_existing_lyrics(str(tmp_path / "faixa.flac"))
        assert resultado["is_bilingual"] is False


# --------------------------------------------------------------------
# fetch_qobuz_lyrics_raw -- client totalmente falso, nunca chama a
# assinatura/sessão de verdade.
# --------------------------------------------------------------------
def _fake_client(status_meta=200, meta=None, status_letra=200, letra=None):
    session = SimpleNamespace(
        request=AsyncMock(
            return_value=SimpleNamespace(
                status_code=status_meta, json=lambda: meta or {}
            )
        ),
        get=AsyncMock(
            return_value=SimpleNamespace(
                status_code=status_letra, json=lambda: letra or {}
            )
        ),
    )
    return SimpleNamespace(
        session=session,
        base="https://exemplo.invalido/api/",
        sec="segredo-falso-nunca-usado-de-verdade",
        _modern_sig=lambda endpoint, params, sec: "assinatura-falsa",
    )


class TestFetchQobuzLyricsRaw:
    async def test_sucesso_devolve_json_da_letra(self):
        client = _fake_client(
            meta={"url": "https://exemplo.invalido/letra.json"},
            letra={"conteudo": _PLACEHOLDER},
        )
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado == {"conteudo": _PLACEHOLDER}

    async def test_primeira_requisicao_falha_devolve_none(self):
        client = _fake_client(status_meta=404)
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado is None

    async def test_usa_lyrics_url_quando_url_ausente(self):
        client = _fake_client(
            meta={"lyrics_url": "https://exemplo.invalido/letra.json"},
            letra={"ok": True},
        )
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado == {"ok": True}

    async def test_usa_qualquer_chave_que_contenha_url_como_fallback(self):
        client = _fake_client(
            meta={"algum_campo_url_estranho": "https://exemplo.invalido/letra.json"},
            letra={"ok": True},
        )
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado == {"ok": True}

    async def test_sem_nenhuma_url_devolve_none(self):
        client = _fake_client(meta={"nada_util": 123})
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado is None

    async def test_segunda_requisicao_falha_devolve_none(self):
        client = _fake_client(
            meta={"url": "https://exemplo.invalido/letra.json"},
            status_letra=500,
        )
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado is None

    async def test_excecao_qualquer_e_capturada_devolve_none(self):
        client = _fake_client()
        client.session.request.side_effect = RuntimeError("erro de rede simulado")
        resultado = await fetch_qobuz_lyrics_raw(client, "track123")
        assert resultado is None

    async def test_idioma_e_incluido_nos_parametros_quando_informado(self):
        client = _fake_client(meta={"nada": 1})
        await fetch_qobuz_lyrics_raw(client, "track123", language="en")
        params_enviados = client.session.request.call_args.kwargs["params"]
        assert params_enviados["language"] == "en"


def _resp(status=200, dados=None):
    return SimpleNamespace(status_code=status, json=lambda: dados or {})


def _client_basico():
    return SimpleNamespace(
        session=object(),
        base="https://exemplo.invalido/api/",
        sec="segredo-falso",
        _modern_sig=lambda endpoint, params, sec: "assinatura-falsa",
    )


def _respostas(monkeypatch, *seq):
    """Troca `request_async` (que já faz os retries) por respostas fixas, em ordem.

    Um item que seja exceção é levantado, como o httpx faz quando a rede cai.
    """
    fila = list(seq)

    async def _falso(client, method, url, **kwargs):
        item = fila.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(retro_tagger, "request_async", _falso)


async def _com_contador():
    """Consulta com um contador de falhas ativo; devolve (resultado, contador)."""
    contador = collections.Counter()
    token = retro_tagger._QOBUZ_FALHAS.set(contador)
    try:
        resultado = await fetch_qobuz_lyrics_raw(_client_basico(), "t1")
        return resultado, contador
    finally:
        retro_tagger._QOBUZ_FALHAS.reset(token)


_META_OK = {"url": "https://exemplo.invalido/letra.json"}


class TestFetchQobuzLyricsRawFalhasQueSobram:
    """Resposta que continua 429/5xx DEPOIS dos retries não é "sem letra"."""

    async def test_429_persistente_registra_limite(self, monkeypatch):
        _respostas(monkeypatch, _resp(429))
        resultado, falhas = await _com_contador()
        assert resultado is None
        assert falhas["limite"] == 1 and not falhas["indisponivel"]

    @pytest.mark.parametrize("status", [408, 425, 500, 503])
    async def test_5xx_e_timeouts_registram_indisponivel(self, monkeypatch, status):
        _respostas(monkeypatch, _resp(status))
        _, falhas = await _com_contador()
        assert falhas["indisponivel"] == 1 and not falhas["limite"]

    async def test_segunda_requisicao_tambem_conta(self, monkeypatch):
        _respostas(monkeypatch, _resp(200, _META_OK), _resp(429))
        resultado, falhas = await _com_contador()
        assert resultado is None
        assert falhas["limite"] == 1

    async def test_404_e_so_sem_letra_e_nao_conta_como_falha(self, monkeypatch):
        _respostas(monkeypatch, _resp(404))
        resultado, falhas = await _com_contador()
        assert resultado is None
        assert not falhas

    async def test_erro_de_rede_registra_indisponivel(self, monkeypatch):
        class TransportError(Exception):
            pass

        _respostas(monkeypatch, TransportError("caiu"))
        resultado, falhas = await _com_contador()
        assert resultado is None
        assert falhas["indisponivel"] == 1

    async def test_erro_que_nao_e_de_rede_nao_conta(self, monkeypatch):
        _respostas(monkeypatch, RuntimeError("bug"))
        resultado, falhas = await _com_contador()
        assert resultado is None
        assert not falhas

    async def test_sucesso_nao_registra_nada(self, monkeypatch):
        _respostas(monkeypatch, _resp(200, _META_OK), _resp(200, {"ok": 1}))
        resultado, falhas = await _com_contador()
        assert resultado == {"ok": 1}
        assert not falhas

    async def test_sem_contador_ativo_nao_quebra(self, monkeypatch):
        _respostas(monkeypatch, _resp(429))
        assert await fetch_qobuz_lyrics_raw(_client_basico(), "t1") is None


class TestDeteccaoDeTraducaoSemMarcador:
    """Original e tradução têm o mesmo timestamp e nenhum símbolo na linha."""

    def test_versos_diferentes_no_mesmo_timestamp_sao_bilingue(self):
        from qobuz_dl.retro_tagger import _has_repeated_timestamp

        lrc = "[00:12.500]First\n[00:12.500]Primeira\n[00:16.200]Second"
        assert _has_repeated_timestamp(lrc) is True

    def test_letra_so_original_nao_e_bilingue(self):
        from qobuz_dl.retro_tagger import _has_repeated_timestamp

        lrc = "[00:12.500]First\n[00:16.200]Second\n[00:20.000]• • •"
        assert _has_repeated_timestamp(lrc) is False

    def test_linha_identica_repetida_nao_conta(self):
        from qobuz_dl.retro_tagger import _has_repeated_timestamp

        assert _has_repeated_timestamp("[00:12.500]La la\n[00:12.500]La la") is False

    def test_entrada_vazia(self):
        from qobuz_dl.retro_tagger import _has_repeated_timestamp

        assert _has_repeated_timestamp("") is False
        assert _has_repeated_timestamp(None) is False
