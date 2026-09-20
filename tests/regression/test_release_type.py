"""Testa a classificação de tipo de lançamento (Album/EP/Single/Compilation/
Live) e a consistência entre os campos "release_type" e "media_type" nos
relatórios de download.

BUG DE REGRESSÃO QUE ESTE ARQUIVO TRAVA
----------------------------------------
`Download._get_track_attr` e `Download._get_album_attr` (downloader.py)
montavam o relatório de cada download com dois campos que deveriam sempre
concordar entre si:

    "release_type": format_release_type(...)          # corrige erros da API
    "media_type": meta.get("product_type", "").capitalize()   # CRU da API

Só o primeiro passava pela heurística de correção (`classify_release_type`,
em utils.py) que existe justamente porque a tag da API vem errada com
frequência (ex.: gravadora marca um lançamento de 5 faixas como "Single").
Resultado: o MESMO relatório podia mostrar "release_type": "Ep" e
"media_type": "Single" pro mesmo álbum -- divergência silenciosa, sem
nenhum erro ou aviso. Corrigido calculando os dois a partir do mesmo
`format_release_type()`. As classes abaixo travam tanto a heurística de
classificação em si quanto essa consistência entre os dois campos.
"""

import pytest

from qobuz_dl.downloader import Download, format_release_type
from qobuz_dl.utils import classify_release_type

pytestmark = pytest.mark.regression


class TestPalavrasChaveTemPrioridadeSobreContagem:
    """Prioridade 1 da docstring de classify_release_type: palavras-chave
    explícitas no título/versão vencem QUALQUER contagem de faixas."""

    def test_live_no_titulo_vence_a_contagem_de_faixas(self):
        assert classify_release_type(title="Unplugged (Live)", track_count=1) == "live"

    def test_live_na_versao(self):
        assert (
            classify_release_type(version="Live at Wembley", track_count=12) == "live"
        )

    def test_compilation_por_titulo(self):
        assert (
            classify_release_type(title="Greatest Hits", track_count=2) == "compilation"
        )

    def test_ep_por_titulo_com_espaco_antes(self):
        assert classify_release_type(title="Some Songs EP", track_count=12) == "ep"

    def test_ep_sozinho_no_titulo_sem_espaco_nao_e_pego_pela_keyword(self):
        # "EP" sem espaço antes (início da string) não bate a checagem
        # `" ep" in base_title` -- cai pra contagem de faixas normalmente.
        # Comportamento real do classify_release_type, travado aqui pra
        # não mudar sem querer numa futura refatoração.
        assert classify_release_type(title="EP", track_count=10) == "album"

    def test_ep_pela_versao_exata(self):
        assert classify_release_type(version="EP", track_count=10) == "ep"


class TestContagemDeFaixasCorrigeATagDaApi:
    """Prioridade 2: <=3 single, 4-7 EP, >7 album -- vale mesmo se a API
    disser outra coisa dentro do proprio trio single/ep/album."""

    def test_ate_3_e_single(self):
        assert (
            classify_release_type(track_count=3, api_release_type="album") == "single"
        )

    def test_de_4_a_7_e_ep(self):
        assert classify_release_type(track_count=5, api_release_type="single") == "ep"

    def test_acima_de_7_e_album(self):
        assert (
            classify_release_type(track_count=8, api_release_type="single") == "album"
        )

    def test_contagem_vence_tag_errada_da_api(self):
        """O caso descrito na docstring de classify_release_type: gravadora
        rotula um lançamento de 5 faixas como "Single" na API."""
        assert classify_release_type(track_count=5, api_release_type="Single") == "ep"


class TestFallbackSemContagemDeFaixas:
    """Prioridade 3: sem contagem de faixas, cai pra duração e por fim
    pra tag crua da API / tipo do item."""

    def test_duracao_longa_vira_album(self):
        assert classify_release_type(track_count=0, duration_seconds=1800) == "album"

    def test_duracao_curta_cai_pra_tag_da_api(self):
        resultado = classify_release_type(
            track_count=0, duration_seconds=100, api_release_type="single"
        )
        assert resultado == "single"

    def test_sem_nada_cai_pro_item_type(self):
        resultado = classify_release_type(
            track_count=0, duration_seconds=0, api_release_type=None, item_type="track"
        )
        assert resultado == "track"


class TestFormatReleaseType:
    """format_release_type() é a camada de exibição por cima de
    classify_release_type() -- usada no nome da pasta de download."""

    def test_ep_fica_em_maiusculas(self):
        assert format_release_type(None, track_count=5) == "EP"

    def test_outros_tipos_ficam_em_title_case(self):
        assert format_release_type(None, track_count=10) == "Album"
        assert format_release_type(None, track_count=2) == "Single"

    def test_desconhecido_vira_texto_amigavel(self):
        assert (
            format_release_type(None, track_count=0, duration_seconds=0)
            == "Desconhecido"
        )


class TestMediaTypeNuncaDivergeDeReleaseType:
    """A regressão em si: os dois campos do relatório de download têm que
    vir SEMPRE do mesmo cálculo. Ver docstring do módulo."""

    def test_get_track_attr(self):
        album_meta = {
            "title": "Um Lancamento Com Cinco Faixas",
            "release_type": "single",  # errado de propósito, como a API às vezes manda
            "version": None,
            "duration": 1200,
        }
        meta = {"album": album_meta, "track_count": 5}

        attrs = Download._get_track_attr(meta, "Faixa X", 24, 96000, "FLAC")

        assert attrs["release_type"] == "EP"
        assert attrs["media_type"] == attrs["release_type"]

    def test_get_album_attr(self):
        meta = {
            "title": "Um Album Inteiro",
            "release_type": "single",
            "version": None,
            "duration": 2400,
            "track_count": 10,
            "artist": {"name": "Artista Exemplo"},
        }

        attrs = Download._get_album_attr(meta, "Um Album Inteiro", "FLAC", 24, 96000)

        assert attrs["release_type"] == "Album"
        assert attrs["media_type"] == attrs["release_type"]

    @pytest.mark.parametrize(
        "track_count,esperado",
        [(2, "Single"), (5, "EP"), (10, "Album")],
    )
    def test_varios_tamanhos_de_lancamento(self, track_count, esperado):
        """Roda a mesma checagem de consistência pra Single/EP/Album -- não
        só pro caso de EP que motivou a correção original."""
        album_meta = {
            "title": "Lancamento Generico",
            "release_type": "compilation",  # tag da API sempre errada de proposito
            "version": None,
            "duration": 200,
        }
        meta = {"album": album_meta, "track_count": track_count}

        attrs = Download._get_track_attr(meta, "Faixa", 16, 44100, "FLAC")

        assert attrs["release_type"] == esperado
        assert attrs["media_type"] == esperado
