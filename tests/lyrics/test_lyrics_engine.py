"""Testes automatizados para lyrics_engine.py.

HISTORIA DESTE ARQUIVO
----------------------
`tests/test_lyrics.py` NAO era um teste pytest -- era um script interativo
com `input()` e `asyncio.run(main())` que chamava a API real do Qobuz.
O pytest o coletava, encontrava zero funcoes `test_*`, e o ignorava em
silencio. O resultado: toda a logica de formacao de LRC (incluindo a
`_build_bilingual_lrc`, que passou por tres implementacoes diferentes
nesta sessao) nunca tinha um unico teste automatizado.

Este arquivo substitui isso com testes que:
1. Nao tocam a rede.
2. Nao precisam de credenciais.
3. Rodam em milissegundos.
4. Travam os comportamentos concretos que importam.

O `test_lyrics.py` original foi movido para `tests/manual/` -- continua
disponivel para verificacao manual com credenciais reais.

BUGS QUE ESTES TESTES TRAVAM
-----------------------------
1. DOIS TIMESTAMPS IDENTICOS SOBRESCREVEM O ORIGINAL: players que indexam
   por timestamp (dict[timestamp] = texto) ficam so com a traducao.
   O teste verifica que o MESMO timestamp aparece duas vezes (uma por
   linha), conforme o formato que o Flacbox reconhece como par.

2. TRADUCAO ANTES DO ORIGINAL: `combined.sort()` usava so x[0] (timestamp),
   entao a ordem de insercao de orig/trans era nao-deterministica dentro
   do mesmo timestamp. Agora usa x[3] (is_translation) como desempate.

3. FUNCAO DUPLICADA EM TEST_LYRICS.PY: havia uma copia de
   `_build_bilingual_lrc` com comportamento LIGEIRAMENTE diferente no
   arquivo de teste manual (usava `~|` em vez de `»`). Qualquer mudanca
   na funcao real nao refletia no teste -- e vice-versa.
"""

import re
import pytest

# ---------------------------------------------------------------------------
# Fixture: acesso a _build_bilingual_lrc sem instanciar LyricsEngine inteiro
# (que exige sessao HTTP, config, etc.)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def build_bilingual():
    """Retorna a funcao _build_bilingual_lrc sem precisar instanciar
    LyricsEngine (que requer sessao HTTP e config do Qobuz)."""
    from qobuz_dl.lyrics_engine import LyricsEngine

    # Cria uma instancia minima sem chamar __init__ de verdade
    obj = LyricsEngine.__new__(LyricsEngine)
    return obj._build_bilingual_lrc


@pytest.fixture(scope="module")
def ms_to_tag():
    from qobuz_dl.lyrics_engine import LyricsEngine

    return LyricsEngine._ms_to_lrc_timestamp


# ---------------------------------------------------------------------------
# Dados de fixture
# ---------------------------------------------------------------------------

ORIGINAL_SIMPLES = (
    "[00:12.340] Hello darkness my old friend\n"
    "[00:15.000] I have come to talk with you again"
)

TRADUCAO_SIMPLES = (
    "[00:12.340] Olá escuridão minha velha amiga\n"
    "[00:15.000] Vim conversar com você outra vez"
)


# ===========================================================================
# _ms_to_lrc_timestamp
# ===========================================================================


class TestMsToLrcTimestamp:
    def test_formato_correto(self, ms_to_tag):
        assert ms_to_tag(0) == "[00:00.000]"

    def test_minutos_corretamente_calculados(self, ms_to_tag):
        tag = ms_to_tag(90000)  # 1 min 30 s
        assert tag.startswith("[01:30.")

    def test_milissegundos_preservados(self, ms_to_tag):
        tag = ms_to_tag(12340)  # 12.340s
        assert "12.340" in tag


# ===========================================================================
# _build_bilingual_lrc: comportamento fundamental
# ===========================================================================


class TestBuildBilingualLrc:
    def test_retorna_original_sem_traducao(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, None)
        assert r == ORIGINAL_SIMPLES

    def test_retorna_original_com_traducao_vazia(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, "")
        assert r == ORIGINAL_SIMPLES

    def test_retorna_traducao_sem_original(self, build_bilingual):
        r = build_bilingual(None, TRADUCAO_SIMPLES)
        assert r == TRADUCAO_SIMPLES

    def test_resultado_nao_e_none(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        assert r is not None

    def test_original_aparece_antes_da_traducao_em_cada_par(self, build_bilingual):
        """Para cada timestamp, a linha original deve vir imediatamente
        antes da linha com o prefixo » do mesmo timestamp."""
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        linhas = r.splitlines()
        for i, linha in enumerate(linhas):
            if "»" in linha and i > 0:
                # A linha anterior deve ter o mesmo timestamp
                tag_atual = re.match(r"(\[\d{2,}:\d{2}\.\d{2,3}\])", linha)
                tag_anterior = re.match(r"(\[\d{2,}:\d{2}\.\d{2,3}\])", linhas[i - 1])
                if tag_atual and tag_anterior:
                    assert tag_atual.group(1) == tag_anterior.group(1), (
                        f"traducao em {tag_atual.group(1)} nao tem original "
                        f"imediatamente antes (linha anterior: {linhas[i - 1]!r})"
                    )

    def test_prefixo_de_traducao_presente(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        linhas_com_traducao = [l for l in r.splitlines() if "»" in l]
        assert len(linhas_com_traducao) == 2

    def test_linhas_originais_sem_prefixo(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        for linha in r.splitlines():
            if "»" not in linha:
                assert "Hello" in linha or "I have" in linha or not linha.strip()

    def test_numero_total_de_linhas(self, build_bilingual):
        """2 pares original+traducao = 4 linhas."""
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        linhas = [l for l in r.splitlines() if l.strip()]
        assert len(linhas) == 4

    def test_ordem_cronologica_preservada(self, build_bilingual):
        r = build_bilingual(ORIGINAL_SIMPLES, TRADUCAO_SIMPLES)
        tags = re.findall(r"\[(\d{2,}):(\d{2})\.(\d{2,3})\]", r)
        timestamps_ms = [
            int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0")[:3])
            for m, s, ms in tags
        ]
        # Cada timestamp deve ser >= ao anterior
        for i in range(1, len(timestamps_ms)):
            assert timestamps_ms[i] >= timestamps_ms[i - 1], (
                f"ordem violada: {timestamps_ms[i - 1]} > {timestamps_ms[i]}"
            )

    def test_traducao_sem_par_nao_quebra(self, build_bilingual):
        """Timestamp na traducao sem correspondente no original."""
        original = "[00:12.340] Linha 1\n[00:15.000] Linha 2"
        traducao = "[00:12.340] Linha 1 PT\n[00:20.500] Extra so na traducao"
        r = build_bilingual(original, traducao)
        assert r is not None
        assert "Extra" in r

    def test_original_sem_par_de_traducao_aparece(self, build_bilingual):
        """Linha original sem correspondente de traducao deve aparecer sozinha."""
        original = "[00:12.340] Linha 1\n[00:15.000] So no original"
        traducao = "[00:12.340] Linha 1 PT"
        r = build_bilingual(original, traducao)
        assert "So no original" in r
        assert "»" not in [l for l in r.splitlines() if "So no original" in l][0]

    def test_timestamps_com_2_casas_decimais_aceitos(self, build_bilingual):
        """O Qobuz as vezes retorna [MM:SS.mm] com 2 digitos."""
        original = "[00:12.34] Hello\n[00:15.00] World"
        traducao = "[00:12.34] Ola\n[00:15.00] Mundo"
        r = build_bilingual(original, traducao)
        assert "Hello" in r
        assert "Ola" in r

    def test_linhas_sem_timestamp_ignoradas(self, build_bilingual):
        """Linhas de metadado LRC ([ti:], [ar:]) nao devem aparecer como faixas."""
        original = "[ti:Album]\n[ar:Artista]\n[00:12.340] Verso real"
        traducao = "[00:12.340] Verso PT"
        r = build_bilingual(original, traducao)
        assert "ti:" not in r
        assert "ar:" not in r
        assert "Verso real" in r

    def test_linhas_vazias_no_lrc_ignoradas(self, build_bilingual):
        """Linhas com timestamp mas texto vazio nao devem gerar pares."""
        original = "[00:12.340] \n[00:15.000] Real"
        traducao = "[00:12.340] \n[00:15.000] PT"
        r = build_bilingual(original, traducao)
        # Apenas as linhas com texto real devem aparecer
        for linha in r.splitlines():
            texto = re.sub(r"\[\d{2,}:\d{2}\.\d{2,3}\]", "", linha).strip()
            texto = texto.lstrip("» ").strip()
            assert texto, f"linha sem texto escapou: {linha!r}"
