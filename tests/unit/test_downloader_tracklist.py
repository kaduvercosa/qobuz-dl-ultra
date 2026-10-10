"""Testa `Download._generate_tracklist` (o "Digital Booklet" .txt do álbum).

Só escreve um arquivo de texto em `tmp_path`; nenhuma rede. `sanitize_filename`
é substituído por uma versão simples porque o que se testa aqui é o CONTEÚDO
do booklet, não a sanitização de nomes (coberta em outro lugar).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from qobuz_dl import downloader
from qobuz_dl.downloader import Download

pytestmark = pytest.mark.unit


def _meta(**extra):
    base = {
        "artist": {"name": "Daft Punk"},
        "composer": {"name": "Thomas Bangalter"},
        "label": {"name": "Virgin"},
        "genre": {"name": "Electronic"},
        "release_date_original": "2001-03-12",
        "tracks": {
            "items": [
                {"track_number": 1, "title": "One More Time", "duration": 320},
                {"track_number": 2, "title": "Aerodynamic", "duration": 212,
                 "parental_warning": True},
            ]
        },
    }
    base.update(extra)
    return base


def _gerar(tmp_path, meta, *, titulo="Discovery", no_credits=False):
    self = SimpleNamespace(no_credits=no_credits)
    with patch.object(
        downloader, "sanitize_filename", lambda nome, **k: str(nome).replace("/", "_")
    ):
        Download._generate_tracklist(self, meta, str(tmp_path), titulo, "FLAC", 24, 96)
    return tmp_path / f"{titulo} - Tracklist.txt"


class TestCabecalho:
    def test_campos_do_album(self, tmp_path):
        texto = _gerar(tmp_path, _meta()).read_text(encoding="utf-8")
        assert "ÁLBUM : Discovery" in texto
        assert "COMPOSITOR : Thomas Bangalter" in texto
        assert "MAIN ART. : Daft Punk" in texto
        assert "RÓTULO : Virgin" in texto
        assert "GÊNERO : Electronic" in texto
        assert "DATA DE LANÇAMENTO : 2001-03-12" in texto
        assert "QUALIDADE : FLAC (24-Bit / 96 kHz)" in texto

    def test_moldura_de_70_colunas(self, tmp_path):
        texto = _gerar(tmp_path, _meta()).read_text(encoding="utf-8")
        assert texto.startswith("=" * 70 + "\n")

    def test_sem_compositor_omite_a_linha(self, tmp_path):
        meta = _meta()
        del meta["composer"]
        assert "COMPOSITOR" not in _gerar(tmp_path, meta).read_text(encoding="utf-8")

    def test_defaults_quando_faltam_dados(self, tmp_path):
        texto = _gerar(tmp_path, {"tracks": {"items": []}}).read_text(encoding="utf-8")
        assert "MAIN ART. : Unknown Artist" in texto
        assert "RÓTULO : Independent" in texto
        assert "GÊNERO : Unknown Genre" in texto
        assert "DATA DE LANÇAMENTO : Unknown Date" in texto

    def test_genero_e_traduzido_pelo_mapa_local(self, tmp_path):
        origem, destino = next(iter(downloader.metadata.LOCAL_GENRE_MAP.items()))
        meta = _meta(genre={"name": origem})
        assert f"GÊNERO : {destino}" in _gerar(tmp_path, meta).read_text(encoding="utf-8")

    def test_genero_fora_do_mapa_fica_como_veio(self, tmp_path):
        meta = _meta(genre={"name": "Genero Inventado XYZ"})
        assert "GÊNERO : Genero Inventado XYZ" in _gerar(tmp_path, meta).read_text(
            encoding="utf-8"
        )

    def test_album_explicito(self, tmp_path):
        texto = _gerar(tmp_path, _meta(parental_warning=True)).read_text(encoding="utf-8")
        assert "ÁLBUM : Discovery [E]" in texto


class TestFaixas:
    def test_numero_titulo_e_duracao(self, tmp_path):
        texto = _gerar(tmp_path, _meta()).read_text(encoding="utf-8")
        assert "[01] One More Time" in texto and "[05:20]" in texto
        assert "[02] Aerodynamic [E]" in texto and "[03:32]" in texto

    def test_duracao_alinhada_na_coluna_62(self, tmp_path):
        linhas = _gerar(tmp_path, _meta()).read_text(encoding="utf-8").splitlines()
        linha = next(l for l in linhas if l.startswith("[01]"))
        assert linha.index("[05:20]") == 61

    def test_sem_performers_usa_o_artista_da_faixa_ou_do_album(self, tmp_path):
        meta = _meta()
        meta["tracks"]["items"][0]["performer"] = {"name": "Convidado"}
        texto = _gerar(tmp_path, meta).read_text(encoding="utf-8")
        assert "  Convidado\n" in texto
        assert "  Daft Punk\n" in texto  # faixa 2 cai no artista do álbum

    def test_performers_viram_lista_com_marcador(self, tmp_path):
        meta = _meta()
        meta["tracks"]["items"][0]["performers"] = (
            "Guy-Manuel de Homem-Christo, Producer - Thomas Bangalter, Mixer\r\nOutro, Vocals"
        )
        texto = _gerar(tmp_path, meta).read_text(encoding="utf-8")
        assert "  * Guy-Manuel de Homem-Christo, Producer\n" in texto
        assert "  * Thomas Bangalter, Mixer\n" in texto
        assert "  * Outro, Vocals\n" in texto

    def test_duracao_ausente_vira_zero(self, tmp_path):
        meta = _meta(tracks={"items": [{"track_number": 1, "title": "Curta"}]})
        assert "[00:00]" in _gerar(tmp_path, meta).read_text(encoding="utf-8")

    def test_titulo_ausente(self, tmp_path):
        meta = _meta(tracks={"items": [{"track_number": 1}]})
        assert "Unknown Title" in _gerar(tmp_path, meta).read_text(encoding="utf-8")


class TestVariosDiscos:
    def _meta_cds(self):
        return _meta(
            media_count=2,
            tracks={
                "items": [
                    {"track_number": 1, "media_number": 1, "title": "A1", "duration": 60},
                    {"track_number": 2, "media_number": 1, "title": "A2", "duration": 60},
                    {"track_number": 1, "media_number": 2, "title": "B1", "duration": 60},
                ]
            },
        )

    def test_separadores_de_disco_e_rotulo_dd_nn(self, tmp_path):
        texto = _gerar(tmp_path, self._meta_cds()).read_text(encoding="utf-8")
        assert texto.count("--- DISC ") == 2
        assert texto.index("--- DISC 1 ---") < texto.index("[01.01] A1")
        assert texto.index("--- DISC 2 ---") < texto.index("[02.01] B1")

    def test_disco_unico_nao_tem_separador(self, tmp_path):
        assert "--- DISC" not in _gerar(tmp_path, _meta()).read_text(encoding="utf-8")

    def test_media_count_ausente_mas_faixas_em_disco_2(self, tmp_path):
        meta = _meta(
            tracks={"items": [{"track_number": 1, "media_number": 2, "title": "X"}]}
        )
        assert "--- DISC 2 ---" in _gerar(tmp_path, meta).read_text(encoding="utf-8")


class TestDescricao:
    def test_html_e_limpo_e_quebrado_em_70_colunas(self, tmp_path):
        meta = _meta(
            description="<p>Primeiro</p><br/>" + ("palavra " * 30) + "<br>Fim <b>negrito</b>"
        )
        texto = _gerar(tmp_path, meta).read_text(encoding="utf-8")
        assert "ÁLBUM REVIEW / NOTES" in texto
        assert "<" not in texto.split("ÁLBUM REVIEW / NOTES", 1)[1]
        assert "Fim negrito" in texto
        notas = texto.split("ÁLBUM REVIEW / NOTES", 1)[1]
        assert all(len(l) <= 70 for l in notas.splitlines())

    def test_sem_descricao_nao_cria_a_secao(self, tmp_path):
        assert "REVIEW" not in _gerar(tmp_path, _meta()).read_text(encoding="utf-8")


class TestQuandoNaoGera:
    def test_no_credits_nao_cria_arquivo(self, tmp_path):
        _gerar(tmp_path, _meta(), no_credits=True)
        assert list(tmp_path.iterdir()) == []

    def test_abort_nao_cria_arquivo(self, tmp_path):
        downloader.abort_event.set()
        try:
            _gerar(tmp_path, _meta())
        finally:
            downloader.abort_event.clear()
        assert list(tmp_path.iterdir()) == []

    def test_arquivo_existente_nao_e_sobrescrito(self, tmp_path):
        alvo = tmp_path / "Discovery - Tracklist.txt"
        alvo.write_text("EDITADO PELO USUARIO", encoding="utf-8")
        _gerar(tmp_path, _meta())
        assert alvo.read_text(encoding="utf-8") == "EDITADO PELO USUARIO"

    def test_pasta_inexistente_nao_levanta(self, tmp_path):
        self = SimpleNamespace(no_credits=False)
        # O erro de escrita é capturado e mostrado, nunca derruba o download.
        with patch.object(downloader, "sanitize_filename", lambda n, **k: str(n)):
            Download._generate_tracklist(
                self, _meta(), str(tmp_path / "nao-existe"), "Discovery", "FLAC", 24, 96
            )
