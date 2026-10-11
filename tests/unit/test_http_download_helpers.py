"""Testa os helpers puros de `qobuz_dl/http_download.py` e `qobuz_dl/fileio.py`.

`tests/network/test_http_download.py` exercita o download inteiro contra um
servidor local (retomada, Range, retentativas). Aqui ficam as peças que não
precisam de socket nenhum: interpretação de cabeçalhos, política de status,
backoff, fábricas de cliente e a promoção final `.part -> destino`.
"""

import hashlib
import os
from types import SimpleNamespace

import pytest

from qobuz_dl import fileio, http_download
from qobuz_dl.http_download import (
    ChecksumMismatchError,
    DownloadAborted,
    PermanentDownloadError,
    _Retry,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Cabeçalhos
# ---------------------------------------------------------------------------
class TestParseContentRange:
    @pytest.mark.parametrize(
        "valor, esperado",
        [
            ("bytes 100-199/1000", (100, 199, 1000)),
            ("bytes 0-0/1", (0, 0, 1)),
            ("bytes 5-9/*", (5, 9, None)),
            ("  BYTES 1-2/3  ", (1, 2, 3)),
            ("Bytes   7-8/9", (7, 8, 9)),
        ],
    )
    def test_formatos_validos(self, valor, esperado):
        assert http_download._parse_content_range(valor) == esperado

    @pytest.mark.parametrize(
        "valor",
        [None, "", "bytes */100", "items 1-2/3", "bytes 1-/3", "bytes a-b/c", "1-2/3"],
    )
    def test_formatos_invalidos(self, valor):
        assert http_download._parse_content_range(valor) is None


class TestParseUnsatisfiedTotal:
    @pytest.mark.parametrize(
        "valor, esperado",
        [("bytes */1234", 1234), (" bytes */0 ", 0), ("BYTES */9", 9)],
    )
    def test_extrai_o_total(self, valor, esperado):
        assert http_download._parse_unsatisfied_total(valor) == esperado

    @pytest.mark.parametrize("valor", [None, "", "bytes 1-2/3", "bytes */*", "x"])
    def test_sem_total(self, valor):
        assert http_download._parse_unsatisfied_total(valor) is None


class TestIntHeader:
    @pytest.mark.parametrize(
        "headers, esperado",
        [
            ({"content-length": "123"}, 123),
            ({"content-length": "0"}, 0),
            ({"content-length": " 5 "}, 5),
        ],
    )
    def test_valores_validos(self, headers, esperado):
        assert http_download._int_header(headers, "content-length") == esperado

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"content-length": ""},
            {"content-length": "abc"},
            {"content-length": "-1"},
        ],
    )
    def test_ausente_invalido_ou_negativo(self, headers):
        assert http_download._int_header(headers, "content-length") is None

    def test_cabecalho_com_valor_none(self):
        assert http_download._int_header({"x": None}, "x") is None


class TestRetryAfter:
    @pytest.mark.parametrize(
        "valor, esperado",
        [("5", 5.0), ("0", 0.0), ("2.5", 2.5), ("-3", 0.0), ("9999", 120.0)],
    )
    def test_segundos_com_piso_e_teto(self, valor, esperado):
        assert http_download._retry_after({"retry-after": valor}) == esperado

    @pytest.mark.parametrize(
        "headers",
        [{}, {"retry-after": ""}, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}],
    )
    def test_data_http_ou_ausente_usa_o_backoff_normal(self, headers):
        assert http_download._retry_after(headers) is None


# ---------------------------------------------------------------------------
# Política de status / abort / backoff
# ---------------------------------------------------------------------------
def _resposta(status, **headers):
    return SimpleNamespace(status_code=status, headers=headers)


class TestCheckStatus:
    @pytest.mark.parametrize("status", [200, 206])
    def test_sucesso_nao_levanta(self, status):
        assert http_download._check_status(_resposta(status)) is None

    @pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504, 599])
    def test_status_temporarios_sao_retentaveis(self, status):
        with pytest.raises(_Retry, match=f"HTTP {status}"):
            http_download._check_status(_resposta(status))

    def test_retry_after_do_servidor_e_repassado(self):
        with pytest.raises(_Retry) as erro:
            http_download._check_status(_resposta(429, **{"retry-after": "7"}))
        assert erro.value.retry_after == 7.0

    def test_sem_retry_after(self):
        with pytest.raises(_Retry) as erro:
            http_download._check_status(_resposta(503))
        assert erro.value.retry_after is None

    def test_404_e_permanente(self):
        with pytest.raises(PermanentDownloadError, match="404"):
            http_download._check_status(_resposta(404))

    @pytest.mark.parametrize("status", [401, 403, 451])
    def test_bloqueios_sao_permanentes_com_mensagem_de_indisponivel(self, status):
        with pytest.raises(PermanentDownloadError, match="indisponível"):
            http_download._check_status(_resposta(status))

    @pytest.mark.parametrize("status", [201, 204, 301, 400, 405, 416])
    def test_demais_status_sao_permanentes(self, status):
        with pytest.raises(PermanentDownloadError, match="inesperada"):
            http_download._check_status(_resposta(status))

    def test_retentavel_e_permanente_sao_tipos_diferentes(self):
        assert not issubclass(_Retry, PermanentDownloadError)
        assert not issubclass(PermanentDownloadError, _Retry)


class TestCheckAbort:
    def test_sem_callback_nao_aborta(self):
        assert http_download._check_abort(None) is None

    def test_callback_falso_nao_aborta(self):
        assert http_download._check_abort(lambda: False) is None

    def test_callback_verdadeiro_aborta(self):
        with pytest.raises(DownloadAborted, match="interrompido"):
            http_download._check_abort(lambda: True)

    def test_aborto_e_um_erro_de_download(self):
        assert issubclass(DownloadAborted, http_download.DownloadError)


class TestBackoff:
    def test_base_zero_ou_negativa_nao_espera(self):
        assert http_download._backoff(3, 0, 10) == 0.0
        assert http_download._backoff(3, -1, 10) == 0.0

    @pytest.mark.parametrize(
        "tentativa, base", [(1, 1.0), (2, 1.0), (3, 1.0), (4, 0.5)]
    )
    def test_cresce_exponencialmente_com_jitter_de_ate_25_por_cento(
        self, tentativa, base
    ):
        minimo = base * 2 ** (tentativa - 1)
        for _ in range(50):
            atraso = http_download._backoff(tentativa, base, 1000.0)
            assert minimo <= atraso <= minimo * 1.25

    def test_respeita_o_teto(self):
        for _ in range(50):
            assert http_download._backoff(20, 1.0, 8.0) <= 8.0 * 1.25

    def test_sem_jitter_quando_o_sorteio_e_zero(self, monkeypatch):
        monkeypatch.setattr(http_download.random, "uniform", lambda a, b: 0.0)
        assert http_download._backoff(3, 2.0, 100.0) == 8.0


# ---------------------------------------------------------------------------
# Fábricas de cliente
# ---------------------------------------------------------------------------
class TestCreateCompatibleClient:
    def test_aceita_tudo_quando_ha_var_keyword(self):
        recebido = {}

        def fabrica(**kw):
            recebido.update(kw)
            return "cliente"

        r = http_download._create_compatible_client(fabrica, {"a": 1, "b": 2})
        assert r == "cliente" and recebido == {"a": 1, "b": 2}

    def test_filtra_o_que_a_fabrica_nao_conhece(self):
        recebido = {}

        def fabrica(timeout=None, headers=None):
            recebido.update(timeout=timeout, headers=headers)
            return "ok"

        http_download._create_compatible_client(
            fabrica, {"timeout": 3, "headers": {"a": "b"}, "http2": True, "limits": 1}
        )
        assert recebido == {"timeout": 3, "headers": {"a": "b"}}

    def test_classe_com_init_reduzido(self):
        class Cliente:
            def __init__(self, timeout=None):
                self.timeout = timeout

        c = http_download._create_compatible_client(
            Cliente, {"timeout": 9, "http2": False}
        )
        assert c.timeout == 9

    def test_assinatura_ilegivel_recebe_tudo(self, monkeypatch):
        def explode(obj):
            raise ValueError("sem assinatura")

        monkeypatch.setattr(http_download.inspect, "signature", explode)
        recebido = {}

        def fabrica(**kw):
            recebido.update(kw)

        http_download._create_compatible_client(fabrica, {"x": 1})
        assert recebido == {"x": 1}


@pytest.fixture
def fabricas(monkeypatch):
    """Troca AsyncClient/Client/Limits por registradores e fixa o suporte a h2."""
    registro = {}

    def _fabrica(nome):
        def criar(**kw):
            registro[nome] = kw
            return SimpleNamespace(nome=nome, **kw)

        return criar

    monkeypatch.setattr(
        http_download.httpx, "AsyncClient", _fabrica("async"), raising=False
    )
    monkeypatch.setattr(http_download.httpx, "Client", _fabrica("sync"), raising=False)
    monkeypatch.setattr(
        http_download.httpx, "Limits", lambda **kw: ("limits", kw), raising=False
    )

    def h2(disponivel):
        monkeypatch.setattr(
            http_download.importlib.util,
            "find_spec",
            lambda nome: object() if disponivel else None,
        )

    h2(False)
    return SimpleNamespace(registro=registro, h2=h2)


class TestMakeClient:
    def test_valores_padrao(self, fabricas):
        http_download.make_client()
        kw = fabricas.registro["async"]
        assert kw["http2"] is False
        assert kw["follow_redirects"] is True
        assert kw["headers"] is None
        assert kw["timeout"] is http_download.DEFAULT_TIMEOUT
        assert kw["limits"] == (
            "limits",
            {
                "max_connections": 32,
                "max_keepalive_connections": 16,
                "keepalive_expiry": 30.0,
            },
        )

    def test_http2_automatico_liga_quando_h2_existe(self, fabricas):
        fabricas.h2(True)
        http_download.make_client()
        assert fabricas.registro["async"]["http2"] is True

    def test_http2_pedido_sem_h2_cai_para_http1(self, fabricas):
        http_download.make_client(http2=True)
        assert fabricas.registro["async"]["http2"] is False

    def test_http2_desligado_explicitamente(self, fabricas):
        fabricas.h2(True)
        http_download.make_client(http2=False)
        assert fabricas.registro["async"]["http2"] is False

    def test_cabecalhos_viram_dict_novo(self, fabricas):
        original = {"User-Agent": "x"}
        http_download.make_client(headers=original)
        enviado = fabricas.registro["async"]["headers"]
        assert enviado == original and enviado is not original

    def test_limites_e_timeout_personalizados(self, fabricas):
        http_download.make_client(
            max_connections=4,
            max_keepalive_connections=2,
            keepalive_expiry=5.0,
            timeout="meu-timeout",
            follow_redirects=False,
        )
        kw = fabricas.registro["async"]
        assert kw["timeout"] == "meu-timeout"
        assert kw["follow_redirects"] is False
        assert kw["limits"][1] == {
            "max_connections": 4,
            "max_keepalive_connections": 2,
            "keepalive_expiry": 5.0,
        }

    def test_fabrica_reduzida_nao_quebra(self, monkeypatch):
        recebido = {}

        def so_headers(headers=None):
            recebido["headers"] = headers
            return "ok"

        monkeypatch.setattr(
            http_download.httpx, "AsyncClient", so_headers, raising=False
        )
        monkeypatch.setattr(
            http_download.httpx, "Limits", lambda **kw: kw, raising=False
        )
        assert http_download.make_client(headers={"a": "b"}) == "ok"
        assert recebido == {"headers": {"a": "b"}}


class TestMakeSyncClient:
    def test_timeout_padrao_e_30s(self, fabricas):
        http_download.make_sync_client()
        assert fabricas.registro["sync"]["timeout"] == 30.0

    def test_timeout_zero_e_respeitado(self, fabricas):
        http_download.make_sync_client(timeout=0)
        assert fabricas.registro["sync"]["timeout"] == 0

    def test_mesma_politica_de_http2(self, fabricas):
        http_download.make_sync_client(http2=True)
        assert fabricas.registro["sync"]["http2"] is False
        fabricas.h2(True)
        http_download.make_sync_client()
        assert fabricas.registro["sync"]["http2"] is True

    def test_usa_o_client_sincrono_e_nao_o_assincrono(self, fabricas):
        http_download.make_sync_client()
        assert "sync" in fabricas.registro and "async" not in fabricas.registro


# ---------------------------------------------------------------------------
# Finalização (.part -> destino)
# ---------------------------------------------------------------------------
class TestFlushAndFsync:
    def test_descarrega_e_sincroniza(self, tmp_path):
        with open(tmp_path / "a.bin", "wb") as f:
            f.write(b"dados")
            http_download._flush_and_fsync(f)
            assert (tmp_path / "a.bin").read_bytes() == b"dados"


class TestFinalize:
    @staticmethod
    def _parte(tmp_path, conteudo=b"conteudo"):
        parte = tmp_path / "faixa.flac.part"
        parte.write_bytes(conteudo)
        return str(parte), str(tmp_path / "faixa.flac")

    def test_promove_sem_checksum(self, tmp_path):
        parte, dest = self._parte(tmp_path)
        assert http_download._finalize(parte, dest, None, False) is None
        assert open(dest, "rb").read() == b"conteudo"
        assert not os.path.exists(parte)

    def test_devolve_o_digest_quando_pedido(self, tmp_path):
        parte, dest = self._parte(tmp_path)
        esperado = hashlib.sha256(b"conteudo").hexdigest()
        assert http_download._finalize(parte, dest, None, True) == esperado

    def test_checksum_correto_promove(self, tmp_path):
        parte, dest = self._parte(tmp_path)
        esperado = hashlib.sha256(b"conteudo").hexdigest()
        assert http_download._finalize(parte, dest, esperado, False) == esperado
        assert os.path.exists(dest)

    def test_checksum_aceita_maiuscula_e_espacos(self, tmp_path):
        parte, dest = self._parte(tmp_path)
        esperado = hashlib.sha256(b"conteudo").hexdigest()
        http_download._finalize(parte, dest, f"  {esperado.upper()} ", False)
        assert os.path.exists(dest)

    def test_checksum_errado_nao_promove_e_mantem_a_parte(self, tmp_path):
        parte, dest = self._parte(tmp_path)
        with pytest.raises(ChecksumMismatchError, match="SHA-256 difere"):
            http_download._finalize(parte, dest, "0" * 64, False)
        assert os.path.exists(parte) and not os.path.exists(dest)

    def test_substitui_destino_existente(self, tmp_path):
        parte, dest = self._parte(tmp_path, b"novo")
        open(dest, "wb").write(b"antigo")
        http_download._finalize(parte, dest, None, False)
        assert open(dest, "rb").read() == b"novo"


# ---------------------------------------------------------------------------
# fileio
# ---------------------------------------------------------------------------
class TestFsyncDir:
    def test_pasta_existente_nao_levanta(self, tmp_path):
        assert fileio.fsync_dir(tmp_path) is None

    def test_pasta_inexistente_e_ignorada(self, tmp_path):
        assert fileio.fsync_dir(tmp_path / "nao-existe") is None

    def test_aceita_str_e_path(self, tmp_path):
        fileio.fsync_dir(str(tmp_path))
        fileio.fsync_dir(tmp_path)

    def test_erro_no_fsync_e_ignorado_e_o_descritor_e_fechado(
        self, tmp_path, monkeypatch
    ):
        fechados = []
        def fsync_falha(fd):
            raise OSError("sem suporte")

        monkeypatch.setattr(fileio.os, "fsync", fsync_falha)
        real_close = os.close
        monkeypatch.setattr(
            fileio.os, "close", lambda fd: (fechados.append(fd), real_close(fd))
        )
        fileio.fsync_dir(tmp_path)
        assert len(fechados) == 1

    def test_no_windows_nao_faz_nada(self, tmp_path, monkeypatch):
        abertos = []
        monkeypatch.setattr(fileio.os, "name", "nt")
        monkeypatch.setattr(fileio.os, "open", lambda *a, **k: abertos.append(a))
        fileio.fsync_dir(tmp_path)
        assert abertos == []


class TestUniqueTmp:
    def test_fica_na_pasta_pedida_e_tem_o_sufixo_temporario(self, tmp_path):
        caminho = fileio._unique_tmp(str(tmp_path))
        assert os.path.dirname(caminho) == str(tmp_path)
        assert caminho.endswith(fileio._ATOMIC_TMP_SUFFIX)
        assert os.path.basename(caminho).startswith(".")

    def test_nomes_nao_colidem(self, tmp_path):
        nomes = {fileio._unique_tmp(str(tmp_path)) for _ in range(200)}
        assert len(nomes) == 200

    def test_nome_curto_o_bastante_para_o_limite_de_255_bytes(self, tmp_path):
        assert len(os.path.basename(fileio._unique_tmp(str(tmp_path)))) < 100

    def test_inclui_o_pid_do_processo(self, tmp_path):
        assert f".{os.getpid()}-" in fileio._unique_tmp(str(tmp_path))
