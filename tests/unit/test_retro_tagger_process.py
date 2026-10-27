"""Testa a árvore de decisão de process_retroactive_lyrics_async e o ponto de
entrada inject_lyrics_retroactively (retro_tagger.py).

Tudo que toca rede, disco de áudio ou engine de letras é substituído:
FLAC/ID3 (tags), extract_track_id, inspect_existing_lyrics (estado atual da
letra), fetch_qobuz_lyrics_raw (resposta do Qobuz) e LyricsEngine. O que se
verifica é a DECISÃO: qual status vai pro relatório e o que foi injetado.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen import id3

from qobuz_dl import retro_tagger as rt

pytestmark = pytest.mark.unit


def _estado(
    has=False,
    bilingue=False,
    lang=None,
    lrc=False,
    content="letra",
    sync=None,
    source=None,
):
    """Estado atual da letra. `sync`: a letra do arquivo já tem marcas de tempo
    (independente de existir um .lrc ao lado -- `lrc`); se omitido, segue `lrc`
    (nos testes antigos, "tem .lrc" já queria dizer "letra sincronizada").
    `source`: de onde ela veio (None = origem não registrada, arquivo antigo)."""
    if sync is None:
        sync = lrc
    return {
        "has_lyrics": has,
        "is_bilingual": bilingue,
        "is_synced": sync,
        "source": source,
        "content": content if has else "",
        "lrc_exists": lrc,
        "language": lang,
    }


def _orig(lang="pt", sync=True):
    """Resposta do Qobuz. Só letra SINCRONIZADA vale (sync=False = só texto)."""
    linha = {"start": 1.0, "text": "x"} if sync else {"text": "x"}
    return {"original": {"lang": lang, "lines": [linha]}}


class _Engine:
    instancias = []

    def __init__(self, genius_token=None, settings=None):
        self.chamadas = []
        self.fechada = False
        self.sucesso = True
        _Engine.instancias.append(self)

    # Campos extras do retorno (fonte, idioma...) que um teste queira simular.
    extra: dict = {}

    def fetch_and_inject(self, **kwargs):
        self.chamadas.append(kwargs)
        return {"success": self.sucesso, **self.extra}

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
    # Descrições longas quebram de linha conforme a largura do terminal; a
    # continuação sempre começa com 10 espaços, então rejunta para os testes não
    # dependerem da largura.
    rodar.texto = lambda: "\n".join(saida).replace("\n" + " " * 10, " ")
    return rodar


def _engine():
    return _Engine.instancias[-1]


# --------------------------------------------------------------------------
# Letra nativa já no idioma alvo
# --------------------------------------------------------------------------
async def test_idioma_alvo_sem_letra_insere_original(ambiente):
    await ambiente(resposta_orig=_orig("pt"))
    assert "Alterada -- letra inserida" in ambiente.texto()
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_translation_response"] is None
    assert chamada["artist"] == "Artista" and chamada["track"] == "Faixa"


async def test_idioma_alvo_letra_em_outro_idioma_e_corrigida(ambiente):
    await ambiente(
        resposta_orig=_orig("pt"), estados=[_estado(has=True, lang="en", lrc=True)]
    )
    assert "Alterada -- letra trocada pela nativa do Qobuz" in ambiente.texto()
    assert len(_engine().chamadas) == 1


async def test_idioma_alvo_texto_simples_vira_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("pt", sync=True),
        estados=[_estado(has=True, lang="pt", lrc=False)],
    )
    assert "texto simples → sincronizada" in ambiente.texto()


async def test_idioma_alvo_ja_correto_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("pt"), estados=[_estado(has=True, lang="pt", lrc=True)]
    )
    assert _engine().chamadas == []
    assert "já presente e em PT" in ambiente.texto()


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
    assert "Atenção -- não foi possível gravar a letra" in ambiente.texto()


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
    assert "Alterada -- letra trocada pela nativa do Qobuz" in ambiente.texto()


async def test_sem_traducao_upgrade_para_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        estados=[_estado(has=True, lang="en", lrc=False)],
    )
    assert "texto simples → sincronizada" in ambiente.texto()


async def test_sem_traducao_letra_ok_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("en"), estados=[_estado(has=True, lang="en", lrc=True)]
    )
    assert _engine().chamadas == []
    assert "sem tradução no Qobuz no momento" in ambiente.texto()


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
# Tradução sincronizada (só ela vale); `_TRAD_PLANA` não tem marcas de tempo.
_TRAD = {"lines": [{"start": 1.0, "text": "t"}]}
_TRAD_PLANA = {"lines": [{"text": "t"}]}


async def test_bilingue_direto_quando_nao_ha_letra(ambiente):
    await ambiente(resposta_orig=_orig("en"), resposta_trad={"translation": _TRAD})
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_translation_response"] == _TRAD
    assert "Alterada -- letra inserida" in ambiente.texto()


async def test_bilingue_corrige_idioma_errado(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="pt", lrc=True)],
    )
    assert "letra trocada pela versão bilíngue do Qobuz" in ambiente.texto()


async def test_bilingue_upgrade_para_sincronizada(ambiente):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", lrc=False)],
    )
    assert "texto simples → sincronizada bilíngue" in ambiente.texto()


async def test_letra_existente_nao_bilingue_ganha_traducao(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=False, lrc=True)],
    )
    assert "tradução adicionada (agora bilíngue)" in ambiente.texto()


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
    assert "não confiável" in ambiente.texto()


async def test_ja_bilingue_nao_altera(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=True, lrc=True)],
    )
    assert _engine().chamadas == []
    assert "já possui letra bilíngue completa" in ambiente.texto()


@pytest.mark.parametrize(
    "estados, esperado",
    [
        ([_estado()], "Atenção -- não foi possível gravar a letra"),
        ([_estado(has=True, lang="pt")], "Falha ao corrigir para bilíngue"),
        (
            [_estado(has=True, lang="en+pt", bilingue=False, lrc=True)],
            "Falha ao atualizar para bilíngue",
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
    assert esperado in ambiente.texto()


# --------------------------------------------------------------------------
# Sem resposta do Qobuz -> fallbacks
# --------------------------------------------------------------------------
async def test_fallback_insere_letra_quando_qobuz_nao_tem(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(), _estado(has=True)])
    assert "letra inserida por outro provedor" in ambiente.texto()
    assert _engine().chamadas[0]["qobuz_lyrics_response"] is None


async def test_fallback_sem_resultado_vira_nao_encontrado(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado()], sucesso=False)
    assert "Atenção -- nenhuma letra encontrada" in ambiente.texto()


async def test_sem_qobuz_mas_com_letra_nao_altera(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(has=True, sync=True)])
    assert _engine().chamadas == []
    assert "o Qobuz não tem letra sincronizada" in ambiente.texto()


async def test_sem_client_nao_consulta_qobuz(ambiente):
    await ambiente(client=None, resposta_orig=_orig("pt"))
    assert "sem Track ID" not in ambiente.texto()
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
    assert "Track ID: 55 (achado por busca" in ambiente.texto()


async def test_busca_textual_rejeita_artista_diferente(ambiente):
    cliente = _Cliente(
        [{"id": 1, "title": "Faixa", "performer": {"name": "Totalmente Outro"}}]
    )
    await ambiente(track_id=None, client=cliente, resposta_orig=None)
    assert "sem Track ID" in ambiente.texto()


async def test_busca_textual_com_erro_e_ignorada(ambiente):
    cliente = _Cliente(erro=RuntimeError("rede"))
    await ambiente(track_id=None, client=cliente, resposta_orig=None)
    assert "sem Track ID" in ambiente.texto()


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
    assert re.search(r"Arquivos analisados\s*: 1\b", ambiente.texto())


async def test_resumo_conta_atualizacoes(ambiente):
    await ambiente(resposta_orig=_orig("pt"))
    texto = ambiente.texto()
    assert "Resumo:" in texto
    assert re.search(r"Atualizados\s*: 1\b", texto)
    assert re.search(r"letras novas inseridas\s*: 1\b", texto)


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


async def test_biblioteca_vazia_avisa_e_fecha_a_engine(tmp_path, monkeypatch):
    saida = []
    _Engine.instancias = []
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": saida.append(t))
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)
    await rt.process_retroactive_lyrics_async(
        str(tmp_path), None, settings=SimpleNamespace()
    )
    assert "Nenhum arquivo .flac/.mp3 encontrado" in "\n".join(saida)
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
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Musicas").mkdir()
    await rt.inject_lyrics_retroactively(
        directory_path="~/Musicas", settings=SimpleNamespace()
    )
    assert Path(espiao.chamadas[0]["directory_path"]) == tmp_path / "Musicas"


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


# --------------------------------------------------------------------------
# Letra JÁ sincronizada (de qualquer provedor) não é "texto simples"
# --------------------------------------------------------------------------
# Regressão: o "upgrade para sincronizada" era decidido por "não existe .lrc ao
# lado do áudio", não pelo conteúdo da letra. Uma letra sincronizada de outro
# provedor, embutida na tag e sem .lrc, era tratada como texto simples e
# sobrescrita com o rótulo "UPGRADE -> SYNC".
async def test_ja_sincronizada_no_idioma_alvo_sem_lrc_nao_e_upgrade(ambiente):
    await ambiente(
        resposta_orig=_orig("pt", sync=True),
        estados=[_estado(has=True, lang="pt", lrc=False, sync=True)],
    )
    assert _engine().chamadas == []
    assert "texto simples" not in ambiente.texto()
    assert "Sem alterações" in ambiente.texto()


async def test_ja_sincronizada_sem_traducao_sem_lrc_nao_e_upgrade(ambiente):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        estados=[_estado(has=True, lang="en", lrc=False, sync=True)],
    )
    assert _engine().chamadas == []
    assert "Sem alterações" in ambiente.texto()


async def test_ja_sincronizada_e_bilingue_sem_lrc_nao_e_sobrescrita(ambiente):
    # O caso que apareceu como "UPGRADE -> SYNC (Original + Tradução)".
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en+pt", bilingue=True, lrc=False, sync=True)],
    )
    assert _engine().chamadas == []
    assert "já possui letra bilíngue completa" in ambiente.texto()
    assert "texto simples" not in ambiente.texto()


async def test_sincronizada_de_outro_provedor_ganha_traducao_sem_dizer_que_era_texto(
    ambiente,
):
    await ambiente(
        resposta_orig=_orig("en", sync=True),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en", bilingue=False, lrc=False, sync=True)],
    )
    texto = ambiente.texto()
    assert len(_engine().chamadas) == 1
    assert "tradução adicionada (agora bilíngue)" in texto
    assert "texto simples" not in texto
    assert "Antes  sincronizada · EN" in texto


async def test_sincronizada_com_idioma_desconhecido_e_trocada_e_diz_o_motivo(ambiente):
    await ambiente(
        resposta_orig=_orig("pt", sync=True),
        estados=[_estado(has=True, lang="unknown", lrc=False, sync=True)],
    )
    texto = ambiente.texto()
    assert len(_engine().chamadas) == 1
    assert "Alterada -- letra trocada pela nativa do Qobuz" in texto
    assert "Idioma da letra atual não identificado" in texto
    assert "texto simples → sincronizada" not in texto


async def test_qobuz_so_com_texto_simples_nao_vale_e_busca_outras_fontes(ambiente):
    await ambiente(
        resposta_orig=_orig("pt", sync=False),
        estados=[_estado(has=True, lang="pt", lrc=False, sync=False)],
    )
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_lyrics_response"] is None  # a do Qobuz é descartada
    assert chamada["better_than"] is None  # texto simples: qualquer fonte vence


async def test_id_nao_confiavel_nao_sobrescreve_e_explica_o_motivo(ambiente):
    cliente = _Cliente([{"id": 7, "title": "Faixa", "performer": {"name": "Artista"}}])
    await ambiente(
        track_id=None,
        client=cliente,
        resposta_orig=_orig("pt"),
        estados=[_estado(has=True, lang="en")],
    )
    assert _engine().chamadas == []
    # A descrição quebra de linha conforme a largura do terminal: normaliza.
    assert "pulado por segurança" in " ".join(ambiente.texto().split())
    assert "Já presente e em PT" not in ambiente.texto()


# --------------------------------------------------------------------------
# Organização da saída (mesmo padrão do comando `tags`)
# --------------------------------------------------------------------------
async def test_cada_faixa_mostra_o_indice_geral_com_zeros_a_esquerda(
    ambiente, tmp_path
):
    for i in range(11):
        (tmp_path / f"extra{i:02d}.flac").write_bytes(b"x")  # + 1 do fixture = 12
    await ambiente(resposta_orig=None, estados=[_estado(has=True, lang="pt")])
    texto = ambiente.texto()
    indices = re.findall(r"^\[(\d+)/12\]", texto, re.M)
    assert indices == [f"{n:02d}" for n in range(1, 13)]
    assert "12 arquivo(s) para verificar" in texto


async def test_cabecalho_do_album_sai_uma_vez_por_pasta(ambiente, tmp_path):
    for pasta in ("A", "B"):
        (tmp_path / pasta).mkdir()
        (tmp_path / pasta / "x.flac").write_bytes(b"x")
        (tmp_path / pasta / "y.flac").write_bytes(b"x")
    tags = {
        "TITLE": ["Faixa"],
        "ARTIST": ["Artista"],
        "ALBUM": ["Meu Álbum"],
        "ALBUMARTIST": ["Banda"],
    }
    await ambiente(
        resposta_orig=None, estados=[_estado(has=True, lang="pt")], tags=tags
    )
    cabecalhos = [l for l in ambiente.saida if l.startswith("== ")]
    assert len(cabecalhos) == 3  # A, B e a pasta raiz (o arquivo do fixture)
    assert sum("Meu Álbum -- Banda  |  2 faixas" in c for c in cabecalhos) == 2
    assert any("|  1 faixa " in c for c in cabecalhos)


async def test_mostra_legenda_de_cores_e_destino_da_gravacao(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(has=True, lang="pt")])
    texto = ambiente.texto()
    assert "Legenda das cores:" in texto
    assert "Gravação    : metadata + arquivo .lrc" in texto


async def test_engine_recebe_indice_da_faixa_e_modo_silencioso(ambiente):
    await ambiente(resposta_orig=_orig("pt"))
    (chamada,) = _engine().chamadas
    assert chamada["track_number"] == 1
    assert chamada["quiet"] is True


async def test_bloco_de_faixa_alterada_mostra_antes_e_agora(ambiente, monkeypatch):
    class _EngineDetalhado(_Engine):
        def fetch_and_inject(self, **kwargs):
            super().fetch_and_inject(**kwargs)
            return {
                "success": True,
                "source": "Qobuz",
                "language": "pt",
                "synchronized": True,
                "bilingual": False,
            }

    monkeypatch.setattr(rt, "LyricsEngine", _EngineDetalhado)
    await ambiente(
        resposta_orig=_orig("pt", sync=True),
        estados=[_estado(has=True, lang="pt", lrc=False, sync=False)],
    )
    texto = ambiente.texto()
    assert "Antes  texto simples · PT" in texto
    assert "Agora  sincronizada · PT · via Qobuz" in texto


# --------------------------------------------------------------------------
# Prioridade: Qobuz bilíngue > Qobuz > BiniLyrics > Musixmatch > LRCLIB
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "origem, prioridade",
    [("BiniLyrics", 3), ("Musixmatch", 4), ("LRCLIB", 5)],
)
async def test_letra_de_fonte_inferior_e_trocada_pela_qobuz_bilingue(
    ambiente, monkeypatch, origem, prioridade
):
    monkeypatch.setattr(
        _Engine,
        "extra",
        {
            "source": "Qobuz",
            "language": "en+pt",
            "synchronized": True,
            "bilingual": True,
        },
    )
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[
            _estado(
                has=True,
                lang="en+pt",
                bilingue=True,
                lrc=True,
                sync=True,
                source=origem,
            )
        ],
    )
    texto = ambiente.texto()
    assert len(_engine().chamadas) == 1
    assert "origem trocada por uma de maior prioridade" in texto
    assert f"via {origem}" in texto  # a origem anterior aparece em "Antes"
    assert "via Qobuz" in texto  # e a nova em "Agora"
    assert f"prioridade {prioridade}" in texto


async def test_binilyrics_sincronizada_perde_para_a_qobuz_sem_traducao(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        estados=[
            _estado(has=True, lang="en", lrc=True, sync=True, source="BiniLyrics")
        ],
    )
    assert len(_engine().chamadas) == 1
    assert "origem trocada por uma de maior prioridade" in ambiente.texto()


async def test_qobuz_sem_traducao_ganha_a_traducao_quando_ela_aparece(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en", lrc=True, sync=True, source="Qobuz")],
    )
    texto = ambiente.texto()
    assert len(_engine().chamadas) == 1
    assert "tradução adicionada (agora bilíngue)" in texto
    assert "origem trocada" not in texto


async def test_qobuz_bilingue_nunca_e_trocada_por_uma_pior(ambiente):
    # O Qobuz agora só tem a original (prioridade 2), mas o arquivo já tem a
    # bilíngue do Qobuz (prioridade 1): fica como está.
    await ambiente(
        resposta_orig=_orig("en"),
        estados=[
            _estado(
                has=True,
                lang="en+pt",
                bilingue=True,
                lrc=True,
                sync=True,
                source="Qobuz",
            )
        ],
    )
    assert _engine().chamadas == []
    assert "Sem alterações" in ambiente.texto()


async def test_traducao_do_qobuz_sem_marcas_de_tempo_nao_vira_bilingue(ambiente):
    await ambiente(
        resposta_orig=_orig("en"), resposta_trad={"translation": _TRAD_PLANA}
    )
    (chamada,) = _engine().chamadas
    assert chamada["qobuz_translation_response"] is None
    assert "sem tradução no Qobuz" in ambiente.texto()


async def test_sem_qobuz_letra_da_lrclib_e_trocada_por_fonte_melhor(ambiente):
    await ambiente(
        resposta_orig=None,
        estados=[_estado(has=True, lang="unknown", sync=True, source="LRCLIB")],
    )
    (chamada,) = _engine().chamadas
    assert chamada["better_than"] == 5  # só consulta fontes melhores que a LRCLIB
    assert chamada["qobuz_lyrics_response"] is None
    assert "origem trocada por uma de maior prioridade" in ambiente.texto()


async def test_sem_qobuz_letra_do_musixmatch_so_aceita_binilyrics(ambiente):
    await ambiente(
        resposta_orig=None,
        estados=[_estado(has=True, lang="unknown", sync=True, source="Musixmatch")],
    )
    (chamada,) = _engine().chamadas
    assert chamada["better_than"] == 4


async def test_sem_qobuz_letra_da_binilyrics_nao_e_tocada(ambiente):
    await ambiente(
        resposta_orig=None,
        estados=[_estado(has=True, lang="en", sync=True, source="BiniLyrics")],
    )
    assert _engine().chamadas == []
    assert "Sem alterações" in ambiente.texto()


async def test_sem_qobuz_sem_fonte_melhor_mantem_a_letra(ambiente):
    await ambiente(
        resposta_orig=None,
        estados=[_estado(has=True, lang="unknown", sync=True, source="LRCLIB")],
        sucesso=False,
    )
    texto = ambiente.texto()
    assert "Sem alterações" in texto
    assert "Nenhuma fonte de maior prioridade que LRCLIB" in texto


async def test_sem_qobuz_texto_simples_aceita_qualquer_fonte_sincronizada(ambiente):
    await ambiente(
        resposta_orig=None, estados=[_estado(has=True, lang="en", sync=False)]
    )
    (chamada,) = _engine().chamadas
    assert chamada["better_than"] is None
    assert "texto simples → sincronizada" in ambiente.texto()


async def test_sem_qobuz_id_nao_confiavel_nao_troca_letra_existente(ambiente):
    await ambiente(
        track_id=None,
        client=None,
        resposta_orig=None,
        estados=[_estado(has=True, lang="unknown", sync=True, source="LRCLIB")],
    )
    assert _engine().chamadas == []


async def test_antes_mostra_a_origem_ou_avisa_que_nao_foi_registrada(ambiente):
    await ambiente(
        resposta_orig=_orig("en"),
        resposta_trad={"translation": _TRAD},
        estados=[_estado(has=True, lang="en", sync=True, source=None)],
    )
    assert "origem não registrada" in ambiente.texto()


async def test_cabecalho_mostra_a_ordem_de_prioridade(ambiente):
    await ambiente(resposta_orig=None, estados=[_estado(has=True, sync=True)])
    assert (
        "Qobuz bilíngue > Qobuz > BiniLyrics > Musixmatch > LRCLIB" in ambiente.texto()
    )
