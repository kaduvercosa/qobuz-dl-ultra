"""Testa a política de troca de capa do comando `tags`.

`metadata.should_replace_embedded_cover` decide se a capa candidata (da
Apple) substitui a que já está embutida no arquivo. A regra é conservadora:
só troca quando a candidata tem MAIS pixels que TODAS as capas atuais, e em
qualquer dúvida (imagem ilegível, erro de leitura) preserva a existente.

As dimensões e as tags vêm de fakes, então os testes não dependem do Pillow
nem do mutagen reais (ambos podem estar quebrados no a-Shell); só o teste de
`_image_dimensions` com imagem válida usa o Pillow e se ignora sem ele.
"""

import io
import os
from types import SimpleNamespace

import pytest

from qobuz_dl import metadata

pytestmark = pytest.mark.unit


class _SemCabecalho(Exception):
    """Substitui `ID3NoHeaderError` (nos fakes não depende do mutagen)."""


class _Apic:
    def __init__(self, data=b"x"):
        self.data = data


class _Outro:
    data = b"nao-e-apic"


@pytest.fixture
def mundo(monkeypatch, tmp_path):
    """Controla o que o mutagen "lê" e quantos pixels cada imagem tem."""
    w = SimpleNamespace(
        dims={},  # bytes da imagem -> (largura, altura) ou None
        flac_pics=[],  # imagens embutidas no FLAC
        flac_erro=None,
        id3_frames=[],  # frames do MP3
        id3_erro=None,
    )

    monkeypatch.setattr(metadata, "_image_dimensions", lambda dados: w.dims.get(dados))
    monkeypatch.setattr(metadata, "ID3NoHeaderError", _SemCabecalho)

    class FlacFalso:
        def __init__(self, caminho):
            if w.flac_erro:
                raise w.flac_erro
            self.pictures = [SimpleNamespace(data=d) for d in w.flac_pics]

    class Id3Falso:
        def __init__(self, caminho):
            if w.id3_erro:
                raise w.id3_erro
            self._frames = {f"k{i}": f for i, f in enumerate(w.id3_frames)}

        def values(self):
            return self._frames.values()

    monkeypatch.setattr(metadata, "FLAC", FlacFalso)
    monkeypatch.setattr(metadata.id3, "ID3", Id3Falso, raising=False)
    monkeypatch.setattr(metadata.id3, "APIC", _Apic, raising=False)

    def candidata(dims, conteudo=b"candidata"):
        caminho = tmp_path / ".apple-cover.jpg"
        caminho.write_bytes(conteudo)
        w.dims[conteudo] = dims
        return str(caminho)

    w.candidata = candidata
    w.pasta = tmp_path
    return w


def _decidir(arquivo, candidata):
    return metadata.should_replace_embedded_cover(arquivo, candidata)


class TestCandidataInvalida:
    @pytest.mark.parametrize("caminho", [None, ""])
    def test_sem_caminho(self, mundo, caminho):
        assert _decidir("a.flac", caminho) is False

    def test_arquivo_inexistente(self, mundo):
        assert _decidir("a.flac", str(mundo.pasta / "nao-existe.jpg")) is False

    def test_caminho_e_uma_pasta(self, mundo):
        assert _decidir("a.flac", str(mundo.pasta)) is False

    def test_dimensoes_ilegiveis(self, mundo):
        assert _decidir("a.flac", mundo.candidata(None)) is False

    def test_erro_inesperado_preserva_a_capa_atual(self, mundo, monkeypatch):
        caminho = mundo.candidata((100, 100))

        def explode(dados):
            raise RuntimeError("pillow quebrado")

        monkeypatch.setattr(metadata, "_image_dimensions", explode)
        assert _decidir("a.flac", caminho) is False


class TestFlac:
    def test_sem_capa_atual_aceita_a_candidata(self, mundo):
        assert _decidir("a.flac", mundo.candidata((500, 500))) is True

    def test_capa_menor_e_substituida(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (300, 300)
        assert _decidir("a.flac", mundo.candidata((1000, 1000))) is True

    def test_mesma_area_nao_substitui(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (500, 500)
        assert _decidir("a.flac", mundo.candidata((500, 500))) is False

    def test_capa_maior_e_preservada(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (3000, 3000)
        assert _decidir("a.flac", mundo.candidata((1000, 1000))) is False

    def test_compara_por_area_nao_por_lado(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (1000, 1000)  # 1.000.000 px
        # Lado maior, mas menos pixels no total: não substitui.
        assert _decidir("a.flac", mundo.candidata((2000, 400))) is False
        # 900x1200 = 1.080.000 px > 1.000.000: substitui, mesmo com um lado menor.
        assert _decidir("a.flac", mundo.candidata((900, 1200), b"c2")) is True

    def test_precisa_superar_todas_as_capas_embutidas(self, mundo):
        mundo.flac_pics = [b"pequena", b"grande"]
        mundo.dims[b"pequena"] = (100, 100)
        mundo.dims[b"grande"] = (2000, 2000)
        assert _decidir("a.flac", mundo.candidata((1000, 1000))) is False
        assert _decidir("a.flac", mundo.candidata((3000, 3000), b"c2")) is True

    def test_capa_atual_ilegivel_nunca_e_destruida(self, mundo):
        mundo.flac_pics = [b"corrompida"]
        mundo.dims[b"corrompida"] = None
        assert _decidir("a.flac", mundo.candidata((9000, 9000))) is False

    def test_uma_ilegivel_entre_varias_ja_preserva(self, mundo):
        mundo.flac_pics = [b"boa", b"corrompida"]
        mundo.dims[b"boa"] = (10, 10)
        mundo.dims[b"corrompida"] = None
        assert _decidir("a.flac", mundo.candidata((9000, 9000))) is False

    def test_imagens_embutidas_vazias_sao_ignoradas(self, mundo):
        mundo.flac_pics = [b"", b""]
        assert _decidir("a.flac", mundo.candidata((100, 100))) is True

    def test_erro_ao_abrir_o_flac_conta_como_sem_capa(self, mundo):
        mundo.flac_erro = OSError("corrompido")
        assert _decidir("a.flac", mundo.candidata((100, 100))) is True

    def test_extensao_desconhecida_usa_o_caminho_do_flac(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (10, 10)
        assert _decidir("a.m4a", mundo.candidata((100, 100))) is True


class TestMp3:
    def test_sem_frames_aceita_a_candidata(self, mundo):
        assert _decidir("a.mp3", mundo.candidata((500, 500))) is True

    def test_apic_menor_e_substituida(self, mundo):
        mundo.id3_frames = [_Apic(b"atual")]
        mundo.dims[b"atual"] = (300, 300)
        assert _decidir("a.mp3", mundo.candidata((1000, 1000))) is True

    def test_apic_maior_e_preservada(self, mundo):
        mundo.id3_frames = [_Apic(b"atual")]
        mundo.dims[b"atual"] = (3000, 3000)
        assert _decidir("a.mp3", mundo.candidata((1000, 1000))) is False

    def test_apic_ilegivel_preserva(self, mundo):
        mundo.id3_frames = [_Apic(b"corrompida")]
        mundo.dims[b"corrompida"] = None
        assert _decidir("a.mp3", mundo.candidata((9000, 9000))) is False

    def test_frames_que_nao_sao_apic_sao_ignorados(self, mundo):
        mundo.id3_frames = [_Outro()]
        assert _decidir("a.mp3", mundo.candidata((100, 100))) is True

    def test_apic_sem_dados_e_ignorada(self, mundo):
        mundo.id3_frames = [_Apic(b"")]
        assert _decidir("a.mp3", mundo.candidata((100, 100))) is True

    def test_arquivo_sem_cabecalho_id3_aceita(self, mundo):
        mundo.id3_erro = _SemCabecalho()
        assert _decidir("a.mp3", mundo.candidata((100, 100))) is True

    def test_erro_de_leitura_aceita(self, mundo):
        mundo.id3_erro = OSError("sem permissão")
        assert _decidir("a.mp3", mundo.candidata((100, 100))) is True

    def test_extensao_em_maiuscula(self, mundo):
        mundo.id3_frames = [_Apic(b"atual")]
        mundo.dims[b"atual"] = (3000, 3000)
        assert _decidir("A.MP3", mundo.candidata((100, 100))) is False

    def test_mp3_nao_consulta_o_flac(self, mundo):
        mundo.flac_erro = RuntimeError("não deveria ser aberto")
        assert _decidir("a.mp3", mundo.candidata((100, 100))) is True


class TestImageDimensions:
    @pytest.mark.parametrize(
        "dados", [b"", b"nao-e-imagem", b"\x89PNG\r\n\x1a\ncorrompido"]
    )
    def test_bytes_invalidos_devolvem_none(self, dados):
        assert metadata._image_dimensions(dados) is None

    def test_imagem_valida_devolve_largura_e_altura(self):
        try:
            from PIL import Image

            Image.new("RGB", (1, 1))
        except Exception:
            pytest.skip("Pillow indisponível neste ambiente")
        buffer = io.BytesIO()
        Image.new("RGB", (37, 21), "red").save(buffer, format="PNG")
        assert metadata._image_dimensions(buffer.getvalue()) == (37, 21)

    def test_funciona_com_arquivo_real_em_disco(self, tmp_path):
        try:
            from PIL import Image

            Image.new("RGB", (1, 1))
        except Exception:
            pytest.skip("Pillow indisponível neste ambiente")
        caminho = tmp_path / "c.jpg"
        Image.new("RGB", (64, 48), "blue").save(caminho, format="JPEG")
        assert metadata._image_dimensions(caminho.read_bytes()) == (64, 48)
        assert os.path.getsize(caminho) > 0


# --------------------------------------------------------------------
# Leitura de dimensões SEM Pillow (a-Shell: Pillow é opcional)
# --------------------------------------------------------------------
def _png(w, h):
    import struct

    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", w, h)
        + b"\x08\x02\x00\x00\x00"
    )


def _jpeg(w, h, com_exif=True):
    import struct

    partes = [b"\xff\xd8"]
    if com_exif:  # segmento APP1 antes do SOF, como nas capas reais
        partes.append(b"\xff\xe1" + struct.pack(">H", 8) + b"Exif\x00\x00")
    partes.append(b"\xff\xc4" + struct.pack(">H", 4) + b"\x00\x00")  # DHT: não é SOF
    partes.append(
        b"\xff\xc2"  # SOF2 (progressivo)
        + struct.pack(">HBHHB", 11, 8, h, w, 1)
        + b"\x01\x11\x00"
    )
    return b"".join(partes) + b"\xff\xd9"


class TestDimensoesPeloCabecalho:
    def test_png(self):
        assert metadata._dimensions_from_header(_png(3000, 2000)) == (3000, 2000)

    def test_jpeg_pula_exif_e_dht_e_le_o_sof(self):
        assert metadata._dimensions_from_header(_jpeg(1400, 1400)) == (1400, 1400)

    def test_jpeg_sem_exif(self):
        dados = _jpeg(600, 400, com_exif=False)
        assert metadata._dimensions_from_header(dados) == (600, 400)

    def test_gif(self):
        import struct

        dados = b"GIF89a" + struct.pack("<HH", 40, 30) + b"\x00" * 6
        assert metadata._dimensions_from_header(dados) == (40, 30)

    def test_webp_vp8x(self):
        dados = (
            b"RIFF\x00\x00\x00\x00WEBPVP8X"
            + b"\x0a\x00\x00\x00"
            + b"\x00\x00\x00\x00"
            + (99).to_bytes(3, "little")
            + (49).to_bytes(3, "little")
        )
        assert metadata._dimensions_from_header(dados) == (100, 50)

    @pytest.mark.parametrize(
        "dados",
        [
            b"",
            None,
            b"nao-e-imagem-nenhuma",
            b"\x89PNG\r\n\x1a\ncorrompido",
            b"\xff\xd8\xff\xd9",  # JPEG sem SOF
            b"\xff\xd8\xff\xc0\x00\x0b\x08\x00",  # SOF truncado
            _png(0, 10),  # dimensão inválida
        ],
    )
    def test_invalidos_devolvem_none(self, dados):
        assert metadata._dimensions_from_header(dados) is None

    def test_image_dimensions_cai_no_cabecalho_sem_pillow(self, monkeypatch):
        import builtins

        real = builtins.__import__

        def sem_pillow(nome, *a, **kw):
            if nome == "PIL" or nome.startswith("PIL."):
                raise ImportError("sem Pillow")
            return real(nome, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", sem_pillow)
        assert metadata._image_dimensions(_jpeg(1200, 1200)) == (1200, 1200)
        assert metadata._image_dimensions(b"lixo") is None

    def test_cabecalho_concorda_com_pillow_quando_ele_existe(self, tmp_path):
        try:
            from PIL import Image

            Image.new("RGB", (1, 1))
        except Exception:
            pytest.skip("Pillow indisponível neste ambiente")
        for formato, tamanho in (("JPEG", (321, 123)), ("PNG", (50, 70))):
            buffer = io.BytesIO()
            Image.new("RGB", tamanho, "green").save(buffer, format=formato)
            dados = buffer.getvalue()
            assert metadata._dimensions_from_header(dados) == tamanho


class TestMotivoDaComparacao:
    def test_candidata_ilegivel_marca_unreadable(self, mundo):
        assert _decidir("a.flac", mundo.candidata(None)) is False
        assert metadata.last_cover_compare_reason() == "unreadable"

    def test_capa_atual_ilegivel_marca_unreadable(self, mundo):
        mundo.flac_pics = [b"atual"]  # sem dims registradas -> None
        assert _decidir("a.flac", mundo.candidata((100, 100))) is False
        assert metadata.last_cover_compare_reason() == "unreadable"

    def test_atual_melhor_nao_e_unreadable(self, mundo):
        mundo.flac_pics = [b"atual"]
        mundo.dims[b"atual"] = (3000, 3000)
        assert _decidir("a.flac", mundo.candidata((100, 100))) is False
        assert metadata.last_cover_compare_reason() is None

    def test_troca_limpa_o_motivo_anterior(self, mundo):
        _decidir("a.flac", mundo.candidata(None))
        assert metadata.last_cover_compare_reason() == "unreadable"
        assert _decidir("a.flac", mundo.candidata((100, 100))) is True
        assert metadata.last_cover_compare_reason() is None
