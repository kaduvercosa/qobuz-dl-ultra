"""Garante que o CI tem as ferramentas REAIS que alguns testes usam.

Vários testes (`test_tags_roundtrip_real`, `test_metadata_shrink_ffmpeg`,
`test_library_report`, `test_name_suggest`) geram FLAC/MP3 e capas de verdade
com o ffmpeg e usam o mutagen/Pillow reais. Na máquina de quem desenvolve (e no
a-Shell, onde nada disso precisa ser instalado) eles se PULAM sozinhos quando a
ferramenta não existe. No GitHub isso seria perigoso: o CI ficaria verde sem
testar nada.

Por isso o workflow define QOBUZ_REQUIRE_REAL_TOOLS=1 e instala o ffmpeg. Com a
variável ligada, estes testes FALHAM se faltar alguma ferramenta (ou se ela não
tiver os codecs que os testes usam). Sem a variável, são pulados.
"""

import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.unit

EXIGIDO = os.environ.get("QOBUZ_REQUIRE_REAL_TOOLS") == "1"

exigido = pytest.mark.skipif(
    not EXIGIDO,
    reason="só no CI (QOBUZ_REQUIRE_REAL_TOOLS=1); localmente os testes reais se pulam sozinhos",
)


@exigido
def test_ffmpeg_instalado_com_os_codecs_usados_pelos_testes():
    caminho = shutil.which("ffmpeg")
    assert caminho, "ffmpeg não está no PATH: instale-o no workflow (apt/brew)"
    saida = subprocess.run(
        [caminho, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for codec in ("flac", "libmp3lame", "mjpeg"):
        assert codec in saida, f"o ffmpeg instalado não tem o encoder '{codec}'"


@exigido
def test_mutagen_real_e_nao_simulado():
    from mutagen.flac import FLAC

    assert isinstance(FLAC, type), "mutagen simulado: os testes reais seriam pulados"


@exigido
def test_pillow_instalado_e_funcionando():
    from PIL import Image  # ImportError aqui = extra "covers" não instalado

    imagem = Image.new("RGB", (4, 4))
    assert imagem.size == (4, 4)
