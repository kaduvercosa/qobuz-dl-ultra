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

from types import SimpleNamespace
from unittest.mock import AsyncMock


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
            "content": "",
            "lrc_exists": False,
            "language": None,
        }


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
