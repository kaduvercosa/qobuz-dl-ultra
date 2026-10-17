"""Recompactação de capa SEM Pillow, usando o ffmpeg (caso do a-Shell).

No a-Shell o Pillow não carrega (o núcleo compilado falha no dlopen), mas o
ffmpeg vem embutido. `_shrink_image_to_fit` cai para `_shrink_with_ffmpeg`
nesse caso. As imagens aqui são geradas pelo próprio ffmpeg, então o teste
não depende do Pillow. Pula sozinho se não houver ffmpeg.
"""

import builtins
import shutil
import subprocess

import pytest

from qobuz_dl import metadata

FFMPEG = shutil.which("ffmpeg")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not FFMPEG, reason="ffmpeg indisponível"),
]


def _jpeg_ruidoso(caminho, lado=800):
    """JPEG grande e difícil de comprimir (ruído), gerado pelo ffmpeg."""
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s={lado}x{lado}:d=1,noise=alls=100:allf=t+u",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(caminho),
        ],
        check=True,
        capture_output=True,
    )
    return caminho.stat().st_size


@pytest.fixture
def capa(tmp_path):
    caminho = tmp_path / "grande.jpg"
    tamanho = _jpeg_ruidoso(caminho)
    return caminho, tamanho


@pytest.fixture(autouse=True)
def _ffmpeg_visivel(monkeypatch):
    # O cache de binários pode ter sido preenchido por outro teste.
    monkeypatch.setattr(metadata, "encontrar_binario", lambda nome: FFMPEG)


class TestShrinkWithFfmpeg:
    def test_so_reduzir_a_qualidade_resolve_e_mantem_as_dimensoes(self, capa):
        caminho, tamanho = capa
        resultado = metadata._shrink_with_ffmpeg(str(caminho), tamanho // 2)
        assert resultado is not None and len(resultado) <= tamanho // 2
        assert resultado[:2] == b"\xff\xd8"
        assert metadata._dimensions_from_header(resultado) == (800, 800)

    def test_qualidade_nao_basta_entao_reduz_as_dimensoes(self, capa):
        caminho, _ = capa
        resultado = metadata._shrink_with_ffmpeg(str(caminho), 15_000)
        assert resultado is not None and len(resultado) <= 15_000
        largura, altura = metadata._dimensions_from_header(resultado)
        assert largura < 800 and altura < 800
        assert largura == altura  # a proporção da capa é preservada

    def test_impossivel_devolve_none(self, capa):
        caminho, _ = capa
        assert metadata._shrink_with_ffmpeg(str(caminho), 10) is None

    def test_sem_ffmpeg_devolve_none(self, capa, monkeypatch):
        monkeypatch.setattr(metadata, "encontrar_binario", lambda nome: None)
        assert metadata._shrink_with_ffmpeg(str(capa[0]), 10_000) is None

    def test_arquivo_ilegivel_devolve_none_sem_levantar(self, tmp_path):
        ruim = tmp_path / "ruim.jpg"
        ruim.write_bytes(b"isto nao e uma imagem")
        assert metadata._shrink_with_ffmpeg(str(ruim), 10_000) is None

    def test_nao_deixa_temporarios_para_tras(self, capa, tmp_path):
        import tempfile

        base = tempfile.gettempdir()
        import os

        antes = {n for n in os.listdir(base) if n.startswith("qdl_cover_")}
        metadata._shrink_with_ffmpeg(str(capa[0]), 15_000)
        depois = {n for n in os.listdir(base) if n.startswith("qdl_cover_")}
        assert depois == antes


class TestShrinkImageToFitSemPillow:
    @pytest.fixture
    def sem_pillow(self, monkeypatch):
        real = builtins.__import__

        def bloqueia(nome, *a, **kw):
            if nome == "PIL" or nome.startswith("PIL."):
                raise ImportError("Pillow indisponível (simulado)")
            return real(nome, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", bloqueia)

    def test_cai_para_o_ffmpeg(self, capa, sem_pillow):
        caminho, tamanho = capa
        resultado = metadata._shrink_image_to_fit(str(caminho), tamanho // 2)
        assert resultado is not None and len(resultado) <= tamanho // 2

    def test_sem_pillow_e_sem_ffmpeg_avisa_e_devolve_none(
        self, capa, sem_pillow, monkeypatch, caplog
    ):
        monkeypatch.setattr(metadata, "encontrar_binario", lambda nome: None)
        with caplog.at_level("WARNING"):
            resultado = metadata._shrink_image_to_fit(str(capa[0]), 10_000)
        assert resultado is None
        assert "ffmpeg" in caplog.text and "Pillow" in caplog.text

    def test_pillow_quebrado_no_nucleo_tambem_cai_para_o_ffmpeg(
        self, capa, monkeypatch
    ):
        """Simula falha no núcleo do Pillow sem depender do binário nativo."""
        from types import SimpleNamespace

        def quebra(*a, **kw):
            raise OSError("dlopen falhou (simulado)")

        imagem_simulada = SimpleNamespace(new=quebra)
        monkeypatch.setattr(metadata, "Image", imagem_simulada, raising=False)

        caminho, tamanho = capa
        resultado = metadata._shrink_image_to_fit(str(caminho), tamanho // 2)

        assert resultado is not None
        assert len(resultado) <= tamanho // 2
