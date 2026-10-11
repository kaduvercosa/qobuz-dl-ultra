"""Fallback de certificados em memória (`_ssl_context_from_memory`).

Alguns ambientes móveis (a-Shell no iOS) abrem o bundle PEM mas falham quando
o OpenSSL o carrega pelo caminho, com `EMFILE`. `_create_compatible_client`
então tenta de novo com as CAs lidas em memória.

Regressão coberta: os blocos PEM eram unidos com a sequência literal "barra + n"
(e não com uma quebra de linha). O OpenSSL rejeitava tudo ("no start line"), o código engolia
o erro e relançava o `EMFILE` original: o fallback NUNCA funcionava. Por isso
o teste principal usa o bundle REAL do certifi e confere quantas CAs entram no
contexto; um teste só com mocks teria passado com o defeito.
"""

import errno
import re
import ssl
from types import SimpleNamespace

import pytest

from qobuz_dl import http_download

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _limpa_cache():
    http_download._ssl_context_from_memory.cache_clear()
    yield
    http_download._ssl_context_from_memory.cache_clear()


def _bundle_certifi():
    certifi = pytest.importorskip("certifi")
    caminho = certifi.where()
    with open(caminho, "rb") as f:
        blocos = re.findall(
            rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            f.read(),
            re.DOTALL,
        )
    return caminho, len(blocos)


# ---------------------------------------------------------------------------
# O contexto em si
# ---------------------------------------------------------------------------
class TestSslContextFromMemory:
    def test_carrega_TODAS_as_cas_do_bundle_com_ca_file(self):
        caminho, total = _bundle_certifi()
        assert total > 10  # um bundle de verdade, não um certificado só
        contexto = http_download._ssl_context_from_memory(caminho, None)
        assert len(contexto.get_ca_certs()) == total

    def test_sem_variaveis_usa_o_bundle_do_certifi(self):
        _, total = _bundle_certifi()
        contexto = http_download._ssl_context_from_memory(None, None)
        assert len(contexto.get_ca_certs()) == total

    def test_a_validacao_continua_obrigatoria(self):
        caminho, _ = _bundle_certifi()
        contexto = http_download._ssl_context_from_memory(caminho, None)
        assert contexto.verify_mode == ssl.CERT_REQUIRED
        assert contexto.check_hostname is True

    def test_bundle_com_comentarios_e_bytes_nao_ascii(self, tmp_path):
        caminho, total = _bundle_certifi()
        with open(caminho, "rb") as f:
            blocos = re.findall(
                rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                f.read(),
                re.DOTALL,
            )[:3]
        sujo = tmp_path / "bundle.pem"
        sujo.write_bytes(
            b"# Emissor: caf\xc3\xa9 \xe2\x80\x93 comentario\n\n"
            + b"\n# outro\xff\n".join(blocos)
            + b"\n"
        )
        contexto = http_download._ssl_context_from_memory(str(sujo), None)
        assert len(contexto.get_ca_certs()) == 3

    def test_arquivo_sem_certificados(self, tmp_path):
        vazio = tmp_path / "vazio.pem"
        vazio.write_text("isto nao e um certificado\n")
        with pytest.raises(ValueError, match="Nenhum certificado PEM"):
            http_download._ssl_context_from_memory(str(vazio), None)

    def test_arquivo_inexistente(self, tmp_path):
        with pytest.raises(OSError):
            http_download._ssl_context_from_memory(str(tmp_path / "nao.pem"), None)

    def test_ca_dir_sozinho_nao_le_o_certifi(self, tmp_path, monkeypatch):
        # Com SSL_CERT_DIR definido e sem arquivo, só o diretório é usado.
        chamadas = []
        real = ssl.SSLContext.load_verify_locations

        def espiao(self, cafile=None, capath=None, cadata=None):
            chamadas.append((cafile, capath, cadata))
            return real(self, cafile=cafile, capath=capath, cadata=cadata)

        monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", espiao)
        http_download._ssl_context_from_memory(None, str(tmp_path))
        assert chamadas == [(None, str(tmp_path), None)]

    def test_resultado_e_cacheado_por_argumentos(self):
        caminho, _ = _bundle_certifi()
        a = http_download._ssl_context_from_memory(caminho, None)
        b = http_download._ssl_context_from_memory(caminho, None)
        assert a is b


# ---------------------------------------------------------------------------
# O fallback em _create_compatible_client
# ---------------------------------------------------------------------------
def _emfile():
    return OSError(errno.EMFILE, "Too many open files")


class _Fabrica:
    """Falha com `erro` na primeira chamada (a sem `verify`) e depois funciona."""

    def __init__(self, erro=None, sempre=False):
        self.erro = erro
        self.sempre = sempre
        self.chamadas = []

    def __call__(self, **kw):
        self.chamadas.append(kw)
        if self.erro is not None and ("verify" not in kw or self.sempre):
            raise self.erro
        return ("cliente", kw)


@pytest.fixture
def contexto_falso(monkeypatch):
    marcador = object()
    pedidos = []

    def fake(ca_file, ca_dir):
        pedidos.append((ca_file, ca_dir))
        return marcador

    monkeypatch.setattr(http_download, "_ssl_context_from_memory", fake)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    return SimpleNamespace(marcador=marcador, pedidos=pedidos)


class TestFallbackEmfile:
    def test_emfile_tenta_de_novo_com_o_contexto_em_memoria(self, contexto_falso):
        fabrica = _Fabrica(_emfile())
        r = http_download._create_compatible_client(fabrica, {"timeout": 5})
        assert r[1] == {"timeout": 5, "verify": contexto_falso.marcador}
        assert len(fabrica.chamadas) == 2
        assert "verify" not in fabrica.chamadas[0]

    def test_sem_emfile_o_erro_original_propaga_sem_fallback(self, contexto_falso):
        fabrica = _Fabrica(OSError(errno.ENOENT, "nao existe"))
        with pytest.raises(OSError) as erro:
            http_download._create_compatible_client(fabrica, {})
        assert erro.value.errno == errno.ENOENT
        assert len(fabrica.chamadas) == 1 and contexto_falso.pedidos == []

    def test_erro_que_nao_e_oserror_nao_e_capturado(self, contexto_falso):
        class Outro(Exception):
            pass

        with pytest.raises(Outro):
            http_download._create_compatible_client(_Fabrica(Outro()), {})
        assert contexto_falso.pedidos == []

    def test_verify_explicito_do_chamador_nunca_e_sobrescrito(self, contexto_falso):
        fabrica = _Fabrica(_emfile(), sempre=True)
        with pytest.raises(OSError) as erro:
            http_download._create_compatible_client(fabrica, {"verify": "meu"})
        assert erro.value.errno == errno.EMFILE
        assert len(fabrica.chamadas) == 1

    def test_falha_ao_montar_o_contexto_relanca_o_emfile_original(
        self, monkeypatch, contexto_falso
    ):
        def quebra(ca_file, ca_dir):
            raise ValueError("bundle ilegivel")

        monkeypatch.setattr(http_download, "_ssl_context_from_memory", quebra)
        with pytest.raises(OSError) as erro:
            http_download._create_compatible_client(_Fabrica(_emfile()), {})
        assert erro.value.errno == errno.EMFILE
        assert erro.value.__cause__ is None  # `from None`: sem ruído encadeado

    def test_segunda_falha_depois_do_fallback_propaga(self, contexto_falso):
        fabrica = _Fabrica(_emfile(), sempre=True)
        with pytest.raises(OSError):
            http_download._create_compatible_client(fabrica, {})
        assert len(fabrica.chamadas) == 2

    def test_variaveis_de_ambiente_sao_repassadas_ao_carregador(
        self, monkeypatch, contexto_falso
    ):
        monkeypatch.setenv("SSL_CERT_FILE", "/x/ca.pem")
        monkeypatch.setenv("SSL_CERT_DIR", "/x/certs")
        http_download._create_compatible_client(_Fabrica(_emfile()), {})
        assert contexto_falso.pedidos == [("/x/ca.pem", "/x/certs")]

    def test_variaveis_vazias_viram_none(self, monkeypatch, contexto_falso):
        monkeypatch.setenv("SSL_CERT_FILE", "")
        http_download._create_compatible_client(_Fabrica(_emfile()), {})
        assert contexto_falso.pedidos == [(None, None)]

    def test_fabrica_que_nao_aceita_verify_relanca_o_emfile(self, contexto_falso):
        chamadas = []

        def reduzida(timeout=None):
            chamadas.append(timeout)
            raise _emfile()

        with pytest.raises(OSError) as erro:
            http_download._create_compatible_client(reduzida, {"timeout": 3})
        assert erro.value.errno == errno.EMFILE
        assert chamadas == [3]  # sem segunda tentativa inútil

    def test_fabrica_reduzida_que_aceita_verify_recebe_so_o_que_conhece(
        self, contexto_falso
    ):
        recebido = []

        def reduzida(timeout=None, verify=True):
            recebido.append((timeout, verify))
            if verify is True:
                raise _emfile()
            return "ok"

        r = http_download._create_compatible_client(
            reduzida, {"timeout": 3, "http2": True}
        )
        assert r == "ok"
        assert recebido == [(3, True), (3, contexto_falso.marcador)]

    def test_assinatura_ilegivel_tambem_tem_fallback(self, monkeypatch, contexto_falso):
        def sem_assinatura(obj):
            raise ValueError("builtin")

        monkeypatch.setattr(http_download.inspect, "signature", sem_assinatura)
        fabrica = _Fabrica(_emfile())
        r = http_download._create_compatible_client(fabrica, {"a": 1})
        assert r[1] == {"a": 1, "verify": contexto_falso.marcador}

    def test_assinatura_ilegivel_com_erro_diferente_propaga(
        self, monkeypatch, contexto_falso
    ):
        def sem_assinatura(obj):
            raise TypeError("builtin")

        monkeypatch.setattr(http_download.inspect, "signature", sem_assinatura)
        with pytest.raises(OSError) as erro:
            http_download._create_compatible_client(
                _Fabrica(OSError(errno.EACCES, "negado")), {}
            )
        assert erro.value.errno == errno.EACCES
        assert contexto_falso.pedidos == []

    def test_sem_falha_nao_toca_no_contexto(self, contexto_falso):
        fabrica = _Fabrica()
        r = http_download._create_compatible_client(fabrica, {"timeout": 1})
        assert r[1] == {"timeout": 1}
        assert contexto_falso.pedidos == []


class TestFallbackComContextoReal:
    def test_fluxo_completo_com_o_bundle_real(self, monkeypatch):
        """EMFILE -> contexto REAL do certifi -> o cliente recebe `verify`."""
        _bundle_certifi()
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        fabrica = _Fabrica(_emfile())
        r = http_download._create_compatible_client(fabrica, {"timeout": 2})
        contexto = r[1]["verify"]
        assert isinstance(contexto, ssl.SSLContext)
        assert len(contexto.get_ca_certs()) > 10
