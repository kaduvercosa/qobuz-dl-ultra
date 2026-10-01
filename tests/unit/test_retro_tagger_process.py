"""Testa a árvore de decisão de process_retroactive_lyrics_async e o ponto de
entrada inject_lyrics_retroactively (retro_tagger.py).

Tudo que toca rede, disco de áudio ou engine de letras é substituído:
FLAC/ID3 (tags), extract_track_id, inspect_existing_lyrics (estado atual da
letra), fetch_qobuz_lyrics_raw (resposta do Qobuz) e LyricsEngine. O que se
verifica é a DECISÃO: qual status vai pro relatório e o que foi injetado.
"""

from types import SimpleNamespace

import pytest
from mutagen import id3

from qobuz_dl import retro_tagger as rt

pytestmark = pytest.mark.unit


def _estado(has=False, bilingue=False, lang=None, lrc=False, content="letra"):
    return {
        "has_lyrics": has,
        "is_bilingual": bilingue,
        "content": content if has else "",
        "lrc_exists": lrc,
        "language": lang,
    }


def _orig(lang="pt", sync=False):
    linha = {"start": 1.0, "text": "x"} if sync else {"text": "x"}
    return {"original": {"lang": lang, "lines": [linha]}}


class _Engine:
    instancias = []

    def __init__(self, genius_token=None, settings=None):
        self.chamadas = []
        self.fechada = False
        self.sucesso = True
        _Engine.instancias.append(self)

    def fetch_and_inject(self, **kwargs):
        self.chamadas.append(kwargs)
        return {"success": self.sucesso}

    def close(self):
        self.fechada = True


class _Cliente:
    def __init__(self, itens=None, erro=None):
        self.itens, self.erro, self.buscas = itens or [], erro, []

    async def search_tracks(self, query, limit=5):
        self.buscas.append(query)
        if self.erro:
            raise self.erro
        return {"tracks": {"items": self.itens}}


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Monta o ambiente e devolve `rodar(**cenario)`."""
    saida = []
    _Engine.instancias = []
    monkeypatch.setattr(rt.ui, "emit", lambda texto="", end="\n": saida.append(texto))
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)

    async def rodar(
        *,
        track_id="100",
        estados=None,
        resposta_orig=None,
        resposta_trad=None,
        target="pt",
        sucesso=True,
        client="padrao",
        arquivo="Artista - Faixa.flac",
        tags=None,
    ):
        caminho = tmp_path / arquivo
        caminho.write_bytes(b"x")
        fila = list(estados or [_estado()])

        def inspecionar(_):
            return fila.pop(0) if len(fila) > 1 else fila[0]

        async def buscar_letra(cli, tid, language=None):
            return resposta_trad if language else resposta_orig

        monkeypatch.setattr(rt, "extract_track_id", lambda p: track_id)
        monkeypatch.setattr(rt, "inspect_existing_lyrics", inspecionar)
        monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar_letra)

        def abrir_flac(p):
            if isinstance(tags, Exception):
                raise tags
            if tags is not None:
                return tags
            return {"TITLE": ["Faixa"], "ARTIST": ["Artista"]}

        monkeypatch.setattr(rt, "FLAC", abrir_flac)
        original_init = _Engine.__init__

        def init(self, *a, **k):
            original_init(self, *a, **k)
            self.sucesso = sucesso

        monkeypatch.setattr(_Engine, "__init__", init)

        settings = SimpleNamespace(
            lyrics_translation_lang=target, lrc_files=True, embed_lyrics=True
        )
        await rt.process_retroactive_lyrics_async(
            str(tmp_path),
            _Cliente() if client == "padrao" else client,
            settings=settings,
        )

    rodar.saida = saida
    rodar.texto = lambda: "\n".join(saida)
    return rodar


def _engine():
    return _Engine.instancias[-1]


# --------------------------------------------------------------------------
# Letra nativa já no idioma alvo
# --------------------------------------------------------------------------
async def test_idioma_alvo_sem_letra_insere_original(ambiente):
    await ambiente(resposta_orig=_orig("pt"))
    assert "Status: ATUALIZADO" in ambiente.texto()
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_translation_response"] is None
    assert chamada["artist"] == "Artista" and chamada["track"] == "Faixa"


async def test_idioma_alvo_letra_em_outro_idioma_e_corrigida(ambiente):
    await ambiente(
        resposta_orig=_orig("pt"), estados=[_estado(has=True, lang="en", lrc=True)]
    )
    assert "Status: CORRIGIDO" in ambiente.texto()
    assert len(_engine().chamadas) == 1


async def test_idioma_alvo_texto_simples_vira_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("pt", sync=True),
        estados=[_estado(has=True, lang="pt", lrc=False)],
    )
    assert "UPGRADE -> SYNC" in ambiente.texto()


async def test_idioma_alvo_ja_correto_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("pt"), estados=[_estado(has=True, lang="pt", lrc=True)]
    )
    assert _engine().chamadas == []
    assert "ja presente e em PT" in ambiente.texto()


async def test_idioma_alvo_id_nao_confiavel_nao_sobrescreve(ambiente):
    cliente = _Cliente([{"id": 7, "title": "Faixa", "performer": {"name": "Artista"}}])
    await ambiente(
        track_id=None,
        client=cliente,
        resposta_orig=_orig("pt"),
        estados=[_estado(has=True, lang="en")],
    )
    assert _engine().chamadas == []


async def test_idioma_alvo_falha_ao_inserir(ambiente):
    await ambiente(resposta_orig=_orig("pt"), sucesso=False)
    assert "Status: FALHA" in ambiente.texto()


async def test_idioma_alvo_falha_ao_corrigir(ambiente):
    await ambiente(
        resposta_orig=_orig("pt"),
        estados=[_estado(has=True, lang="en")],
        sucesso=False,
    )
    assert "Falha ao corrigir letra (Qobuz)" in ambiente.texto()


# --------------------------------------------------------------------------
# Qobuz só tem o original (sem tradução)
# --------------------------------------------------------------------------
async def test_sem_traducao_sem_letra_insere_original(ambiente):
    await ambiente(resposta_orig=_orig("en"))
    assert "Letra original (EN) inserida" in ambiente.texto()


async def test_sem_traducao_corrige_idioma_errado(ambiente):
    await ambiente(
        resposta_orig=_orig("en"), estados=[_estado(has=True, lang="fr", lrc=True)]
    )
    assert "Status: CORRIGIDO" in ambiente.texto()


async def test_sem_traducao_upgrade_para_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        estados=[_estado(has=True, lang="en", lrc=False)],
    )
    assert "UPGRADE -> SYNC" in ambiente.texto()


async def test_sem_traducao_letra_ok_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("en"), estados=[_estado(has=True, lang="en", lrc=True)]
    )
    assert _engine().chamadas == []
    assert "sem traducao no Qobuz no momento" in ambiente.texto()


@pytest.mark.parametrize(
    "estados, esperado",
    [
        ([_estado()], "Falha ao inserir letra original"),
        ([_estado(has=True, lang="fr")], "Falha ao corrigir letra original"),
    ],
)
async def test_sem_traducao_falhas(ambiente, estados, esperado):
    await ambiente(resposta_orig=_orig("en"), estados=estados, sucesso=False)
    assert esperado in ambiente.texto()


# --------------------------------------------------------------------------
# Qobuz tem original + tradução
# --------------------------------------------------------------------------
_TRAD = {"lines": [{"text": "t"}]}


async def test_bilingue_direto_quando_nao_ha_letra(ambiente):
    await ambiente(resposta_orig=_orig("en"), resposta_trad={"translation": _TRAD})
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_translation_response"] == _TRAD
    assert "Status: ATUALIZADO" in ambiente.texto()


async def test_bilingue_corrige_idioma_errado(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="pt", lrc=True)],
    )
    assert "CORRIGIDO -> BILINGUE" in ambiente.texto()


async def test_bilingue_upgrade_para_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", lrc=False)],
    )
    assert "UPGRADE -> SYNC BILINGUE" in ambiente.texto()


async def test_letra_existente_nao_bilingue_ganha_traducao(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=False, lrc=True)],
    )
    assert "ATUALIZADO -> BILINGUE" in ambiente.texto()


async def test_nao_bilingue_com_id_nao_confiavel_e_pulada(ambiente):
    cliente = _Cliente([{"id": 9, "title": "Faixa", "performer": {"name": "Artista"}}])
    await ambiente(
        track_id=None,
        client=cliente,
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=False, lrc=True)],
    )
    assert _engine().chamadas == []
    assert "Track ID nao confiavel" in ambiente.texto()


async def test_ja_bilingue_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=True, lrc=True)],
    )
    assert _engine().chamadas == []
    assert "ja possui letra bilingue completa" in ambiente.texto()


@pytest.mark.parametrize(
    "estados, esperado",
    [
        ([_estado()], "Status: ATUALIZADO"),
        ([_estado(has=True, lang="pt")], "Falha ao corrigir para bilingue"),
        (
            [_estado(has=True, lang="en+pt", bilingue=False, lrc=True)],
            "Falha ao atualizar para bilingue",
        ),
    ],
)
async def test_bilingue_falhas(ambiente, estados, esperado):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=estados,
        sucesso=False,
    )
    texto = ambiente.texto()
    assert "FALHA" in texto or "Falha" in texto
    if esperado != "Status: ATUALIZADO":
        assert esperado in texto


# --------------------------------------------------------------------------
# Sem resposta do Qobuz -> fallbacks
# --------------------------------------------------------------------------
async def test_fallback_insere_letra_quando_qobuz_nao_tem(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(), _estado(has=True)])
    assert "ATUALIZADO (FALLBACK)" in ambiente.texto()
    assert _engine().chamadas[0]["qobuz_lyrics_response"] is None


async def test_fallback_sem_resultado_vira_nao_encontrado(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado()], sucesso=False)
    assert "NÃO ENCONTRADO" in ambiente.texto()


async def test_sem_qobuz_mas_com_letra_nao_altera(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(has=True)])
    assert _engine().chamadas == []
    assert "Qobuz nao possui registros de traducao" in ambiente.texto()


async def test_sem_client_nao_consulta_qobuz(ambiente):
    await ambiente(client=None, resposta_orig=_orig("pt"))
    assert "Sem Track ID" not in ambiente.texto()
    assert _engine().chamadas[0]["qobuz_lyrics_response"] is None


async def test_sem_idioma_alvo_nao_busca_traducao(ambiente):
    await ambiente(resposta_orig=_orig("en"), target="")
    assert "ORIGINAL (Sem traducao forcada)" in ambiente.texto()
    assert "Letra original (EN) inserida" in ambiente.texto()


# --------------------------------------------------------------------------
# Casamento textual quando o arquivo não tem Track ID
# --------------------------------------------------------------------------
async def test_busca_textual_acha_faixa_com_titulo_e_artista(ambiente):
    cliente = _Cliente(
        [
            {"id": 1, "title": "Outra", "performer": {"name": "Outro"}},
            {"id": 55, "title": "Faixa", "performer": {"name": "Artista"}},
        ]
    )
    await ambiente(track_id=None, client=cliente, resposta_orig=_orig("pt"))
    assert cliente.buscas == ["Artista Faixa"]
    assert "[Track ID: 55]" in ambiente.texto()


async def test_busca_textual_rejeita_artista_diferente(ambiente):
    cliente = _Cliente(
        [{"id": 1, "title": "Faixa", "performer": {"name": "Totalmente Outro"}}]
    )
    await ambiente(track_id=None, client=cliente, resposta_orig=None)
    assert "[Sem Track ID]" in ambiente.texto()


async def test_busca_textual_com_erro_e_ignorada(ambiente):
    cliente = _Cliente(erro=RuntimeError("rede"))
    await ambiente(track_id=None, client=cliente, resposta_orig=None)
    assert "[Sem Track ID]" in ambiente.texto()


# --------------------------------------------------------------------------
# Leitura de tags, relatório e limpeza
# --------------------------------------------------------------------------
async def test_titulo_cai_para_nome_do_arquivo_sem_tags(ambiente):
    await ambiente(arquivo="Nome do Arquivo.flac", tags={}, resposta_orig=None)
    assert "Nome do Arquivo" in ambiente.texto()


async def test_flac_ilegivel_nao_derruba_o_processamento(ambiente):
    await ambiente(
        arquivo="corrompido.flac", tags=ValueError("corrompido"), resposta_orig=None
    )
    assert "corrompido" in ambiente.texto()


async def test_mp3_le_titulo_artista_e_album_das_tags_id3(ambiente, monkeypatch):
    tags = id3.ID3()
    tags.add(id3.TIT2(encoding=3, text=["Titulo MP3"]))
    tags.add(id3.TPE1(encoding=3, text=["Artista MP3"]))
    tags.add(id3.TALB(encoding=3, text=["Album MP3"]))
    monkeypatch.setattr(rt.id3, "ID3", lambda p: tags)
    await ambiente(arquivo="faixa.mp3", resposta_orig=None)
    assert "Artista MP3 - Titulo MP3" in ambiente.texto()
    assert _engine().chamadas[0]["album"] == "Album MP3"


async def test_arquivos_nao_de_audio_sao_ignorados(ambiente, tmp_path):
    (tmp_path / "capa.jpg").write_bytes(b"x")
    await ambiente(resposta_orig=None)
    assert "Total de arquivos analisados: 1" in ambiente.texto()


async def test_resumo_conta_atualizacoes(ambiente):
    await ambiente(resposta_orig=_orig("pt"))
    texto = ambiente.texto()
    assert "RESUMO GERAL" in texto
    assert "atualizados" in texto and ": 1" in texto


async def test_engine_e_fechada_mesmo_quando_ocorre_erro(tmp_path, monkeypatch):
    _Engine.instancias = []
    (tmp_path / "a.flac").write_bytes(b"x")

    def explode(_):
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": None)
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)
    monkeypatch.setattr(rt, "extract_track_id", lambda p: None)
    monkeypatch.setattr(rt, "FLAC", lambda p: {})
    monkeypatch.setattr(rt, "inspect_existing_lyrics", explode)
    with pytest.raises(RuntimeError, match="falha inesperada"):
        await rt.process_retroactive_lyrics_async(
            str(tmp_path), None, settings=SimpleNamespace()
        )
    assert _engine().fechada


async def test_biblioteca_vazia_gera_relatorio_zerado(tmp_path, monkeypatch):
    saida = []
    _Engine.instancias = []
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": saida.append(t))
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)
    await rt.process_retroactive_lyrics_async(
        str(tmp_path), None, settings=SimpleNamespace()
    )
    assert "Total de arquivos analisados: 0" in "\n".join(saida)
    assert _engine().fechada


# --------------------------------------------------------------------------
# inject_lyrics_retroactively
# --------------------------------------------------------------------------
@pytest.fixture
def espiao(monkeypatch):
    chamadas, saida = [], []

    async def fake(**kwargs):
        chamadas.append(kwargs)

    monkeypatch.setattr(rt, "process_retroactive_lyrics_async", fake)
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": saida.append(t))
    return SimpleNamespace(chamadas=chamadas, saida=saida)


async def test_inject_usa_pasta_informada(tmp_path, espiao):
    await rt.inject_lyrics_retroactively(
        directory_path=str(tmp_path),
        client="cli",
        genius_token="tok",
        settings=SimpleNamespace(),
    )
    (c,) = espiao.chamadas
    assert c["directory_path"] == str(tmp_path)
    assert c["client"] == "cli" and c["genius_token"] == "tok"


async def test_inject_usa_default_folder_das_settings(tmp_path, espiao):
    await rt.inject_lyrics_retroactively(
        settings=SimpleNamespace(default_folder=str(tmp_path))
    )
    assert espiao.chamadas[0]["directory_path"] == str(tmp_path)


async def test_inject_pasta_inexistente_avisa_e_nao_processa(tmp_path, espiao):
    await rt.inject_lyrics_retroactively(
        directory_path=str(tmp_path / "nao_existe"), settings=SimpleNamespace()
    )
    assert espiao.chamadas == []
    assert "não existe" in "\n".join(espiao.saida)


async def test_inject_expande_til_do_home(tmp_path, espiao, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Musicas").mkdir()
    await rt.inject_lyrics_retroactively(
        directory_path="~/Musicas", settings=SimpleNamespace()
    )
    assert espiao.chamadas[0]["directory_path"] == str(tmp_path / "Musicas")


async def test_inject_propaga_erro_apos_avisar(tmp_path, monkeypatch):
    saida = []

    async def explode(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(rt, "process_retroactive_lyrics_async", explode)
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": saida.append(t))
    with pytest.raises(RuntimeError, match="boom"):
        await rt.inject_lyrics_retroactively(
            directory_path=str(tmp_path), settings=SimpleNamespace()
        )
    assert "falhou: boom" in "\n".join(saida)
