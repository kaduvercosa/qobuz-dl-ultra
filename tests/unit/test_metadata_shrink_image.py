"""Testa qobuz_dl/metadata.py::_shrink_image_to_fit -- recompactação de
capa grande demais pro limite de 16MB do FLAC.

Deliberadamente fora de test_metadata_tagging.py (que evita I/O de
verdade) porque essa função só faz sentido testada com Pillow
processando uma imagem de verdade -- fakes não exercitam a lógica real
de qualidade/resize.
"""

import sys

import pytest

try:
    from PIL import Image
except Exception as _erro_pillow:  # noqa: BLE001 -- amplo de propósito:
    # no iOS/a-Shell o `import PIL` em si funciona (é puro Python), mas
    # `from PIL import Image` dispara `from . import _imaging as core`
    # dentro do próprio Image.py, que tenta um dlopen de um caminho de
    # framework que não existe nesse runtime protegido -- e por algum
    # motivo isso não era capturado por `pytest.importorskip("PIL.Image")`
    # (mesmo sendo um ImportError de verdade). Captura ampla aqui pra não
    # depender de adivinhar exatamente que exceção o iOS decide levantar.
    pytest.skip(
        f"Pillow sem o núcleo compilado (_imaging) funcionando neste "
        f"ambiente: {_erro_pillow}",
        allow_module_level=True,
    )

from qobuz_dl import metadata

pytestmark = pytest.mark.unit


def _criar_jpeg(caminho, tamanho=(2000, 2000), cor=(120, 60, 200), qualidade=95):
    Image.new("RGB", tamanho, color=cor).save(caminho, format="JPEG", quality=qualidade)


class TestShrinkImageToFit:
    def test_reducao_de_qualidade_ja_resolve(self, tmp_path):
        caminho = tmp_path / "grande.jpg"
        _criar_jpeg(caminho)
        tamanho_original = caminho.stat().st_size

        resultado = metadata._shrink_image_to_fit(
            str(caminho), max_bytes=tamanho_original // 2
        )

        assert resultado is not None
        assert len(resultado) <= tamanho_original // 2

    def test_modo_paleta_e_convertido_antes_de_salvar_como_jpeg(self, tmp_path):
        # JPEG não suporta modo "P" (paleta) -- precisa converter pra RGB
        # antes de re-salvar, senão Image.save() explode.
        caminho = tmp_path / "paleta.png"
        Image.new("P", (500, 500)).save(caminho, format="PNG")

        resultado = metadata._shrink_image_to_fit(str(caminho), max_bytes=1_000_000)

        assert resultado is not None

    def test_reducao_de_qualidade_nao_basta_forca_redimensionamento(self, tmp_path):
        caminho = tmp_path / "grande2.jpg"
        _criar_jpeg(caminho, tamanho=(3000, 3000), cor=(10, 200, 30))

        resultado = metadata._shrink_image_to_fit(str(caminho), max_bytes=3000)

        assert resultado is not None
        assert len(resultado) <= 3000

    def test_arquivo_corrompido_devolve_none_sem_levantar(self, tmp_path):
        caminho = tmp_path / "corrompido.jpg"
        caminho.write_bytes(b"isso nao e uma imagem")

        resultado = metadata._shrink_image_to_fit(str(caminho), max_bytes=1000)

        assert resultado is None

    def test_sem_pillow_instalado_devolve_none(self, tmp_path, monkeypatch):
        caminho = tmp_path / "a.jpg"
        _criar_jpeg(caminho)

        monkeypatch.setitem(sys.modules, "PIL", None)
        monkeypatch.setitem(sys.modules, "PIL.Image", None)

        resultado = metadata._shrink_image_to_fit(str(caminho), max_bytes=1000)

        assert resultado is None
