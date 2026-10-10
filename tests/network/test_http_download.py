"""Testes de qobuz_dl/http_download.py com um servidor HTTP SIMULADO.

Nada toca a rede: `FakeClient` imita só o trecho de `httpx.AsyncClient`
que o módulo usa (`stream(...)` + resposta com `status_code`, `headers` e
`aiter_bytes`). Isso permite simular, de forma determinística, as falhas
que quebram downloads reais: conexão que cai no meio, resposta que termina
cedo SEM erro, servidor que ignora Range, 429/503, etc.
"""

import hashlib
import os

import httpx
import pytest

from qobuz_dl import http_download as hd

pytestmark = pytest.mark.network

DADOS = bytes(range(256)) * 40  # 10 240 bytes, conteúdo previsível
ETAG = '"abc123"'


# ---------------------------------------------------------------------------
# Servidor simulado
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._chunks = list(chunks)

    async def aiter_bytes(self, chunk_size=None):
        for item in self._chunks:
            if isinstance(item, Exception):
                raise item
            yield item


class _Stream:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    """`handler(n, url, headers) -> FakeResponse`, n = nº da requisição (1..)."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def stream(self, method, url, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": dict(headers or {})})
        return _Stream(self.handler(len(self.requests), url, dict(headers or {})))


def _fatias(data, tamanho=1000):
    return [data[i : i + tamanho] for i in range(0, len(data), tamanho)]


def servidor(
    data=DADOS,
    *,
    etag=ETAG,
    ignora_range=False,
    sem_length=False,
    falhas=None,
):
    """Servidor que respeita Range. `falhas[n]` altera a n-ésima requisição:

    - ("cai", k): entrega k bytes e levanta ReadError;
    - ("trunca", k): entrega k bytes e TERMINA sem erro (conexão fechada limpa);
    - ("status", codigo[, headers]): responde só com esse status.
    """
    falhas = falhas or {}

    def handler(n, url, headers):
        falha = falhas.get(n)
        if falha and falha[0] == "status":
            extra = falha[2] if len(falha) > 2 else {}
            return FakeResponse(falha[1], extra)

        inicio = 0
        pedido = headers.get("Range", "")
        if pedido.startswith("bytes=") and not ignora_range:
            inicio = int(pedido[6:].split("-")[0] or 0)

        if inicio >= len(data) and not ignora_range and inicio > 0:
            return FakeResponse(416, {"content-range": f"bytes */{len(data)}"})

        corpo = data[inicio:]
        cabecalhos = {}
        if etag:
            cabecalhos["etag"] = etag
        if not sem_length:
            cabecalhos["content-length"] = str(len(corpo))
        if ignora_range or inicio == 0 and not pedido:
            status = 200
        else:
            status = 206
            cabecalhos["content-range"] = (
                f"bytes {inicio}-{len(data) - 1}/{len(data)}"
            )
        if ignora_range:
            corpo, status = data, 200
            cabecalhos["content-length"] = str(len(data))
        if sem_length:
            # Servidor que não sabe o total: sem Content-Length e com "/*".
            cabecalhos.pop("content-length", None)
            if "content-range" in cabecalhos:
                cabecalhos["content-range"] = f"bytes {inicio}-{len(data) - 1}/*"

        if falha and falha[0] in ("cai", "trunca"):
            entregue = _fatias(corpo[: falha[1]])
            if falha[0] == "cai":
                entregue.append(httpx.ReadError("conexão reiniciada"))
            return FakeResponse(status, cabecalhos, entregue)
        return FakeResponse(status, cabecalhos, _fatias(corpo))

    return FakeClient(handler)


class Dormidas:
    """`sleep` falso: registra os atrasos pedidos sem esperar de verdade."""

    def __init__(self):
        self.atrasos = []

    async def __call__(self, segundos):
        self.atrasos.append(segundos)


def _sobras(pasta):
    return [n for n in os.listdir(pasta) if n.endswith((".part", ".atomic.tmp"))]


# ---------------------------------------------------------------------------
# Caminho feliz
# ---------------------------------------------------------------------------


async def test_baixa_arquivo_completo_e_reporta_progresso(tmp_path):
    destino = tmp_path / "faixa.bin"
    progresso = []

    resultado = await hd.download_file(
        servidor(),
        "http://x/f",
        destino,
        on_progress=lambda baixado, total: progresso.append((baixado, total)),
    )

    assert destino.read_bytes() == DADOS
    assert resultado.size == len(DADOS)
    assert resultado.attempts == 1
    assert resultado.size_verified is True
    assert progresso[-1] == (len(DADOS), len(DADOS))
    assert [b for b, _ in progresso] == sorted(b for b, _ in progresso)
    assert _sobras(tmp_path) == []


async def test_cria_pastas_e_sempre_pede_range_e_identity(tmp_path):
    destino = tmp_path / "a" / "b" / "faixa.bin"
    cliente = servidor()

    await hd.download_file(cliente, "http://x/f", destino)

    req = cliente.requests[0]["headers"]
    assert req["Range"] == "bytes=0-"
    assert req["Accept-Encoding"] == "identity"
    assert destino.read_bytes() == DADOS


async def test_parcial_de_execucao_anterior_nunca_e_reaproveitado(tmp_path):
    destino = tmp_path / "faixa.bin"
    (tmp_path / "faixa.bin.part").write_bytes(b"LIXO DE OUTRO TIER")
    cliente = servidor()

    await hd.download_file(cliente, "http://x/f", destino)

    assert cliente.requests[0]["headers"]["Range"] == "bytes=0-"
    assert destino.read_bytes() == DADOS


# ---------------------------------------------------------------------------
# Retomada e integridade (os bugs do código atual)
# ---------------------------------------------------------------------------


async def test_conexao_que_cai_no_meio_e_retomada_por_range(tmp_path):
    destino = tmp_path / "faixa.bin"
    cliente = servidor(falhas={1: ("cai", 3000)})

    resultado = await hd.download_file(
        cliente, "http://x/f", destino, sleep=Dormidas()
    )

    assert destino.read_bytes() == DADOS
    assert resultado.attempts == 2
    segunda = cliente.requests[1]["headers"]
    assert segunda["Range"] == "bytes=3000-"
    assert segunda["If-Range"] == ETAG  # protege contra arquivo trocado no servidor


async def test_resposta_que_termina_cedo_sem_erro_e_retomada(tmp_path):
    """Bug original: terminar antes do tamanho NÃO era retentado."""
    destino = tmp_path / "faixa.bin"
    cliente = servidor(falhas={1: ("trunca", 4500)})

    resultado = await hd.download_file(
        cliente, "http://x/f", destino, sleep=Dormidas()
    )

    assert destino.read_bytes() == DADOS
    assert resultado.attempts == 2
    assert cliente.requests[1]["headers"]["Range"] == "bytes=4500-"


async def test_servidor_que_ignora_range_recomeca_sem_duplicar_bytes(tmp_path):
    destino = tmp_path / "faixa.bin"
    ignora = servidor(ignora_range=True, falhas={1: ("cai", 2000)})

    await hd.download_file(ignora, "http://x/f", destino, sleep=Dormidas())

    # Anexar a resposta 200 ao parcial duplicaria os primeiros bytes.
    assert destino.read_bytes() == DADOS


async def test_content_range_incompativel_recomeca_do_zero(tmp_path):
    destino = tmp_path / "faixa.bin"

    def handler(n, url, headers):
        if n == 1:
            return FakeResponse(
                200,
                {"content-length": str(len(DADOS)), "etag": ETAG},
                _fatias(DADOS[:3000]) + [httpx.ReadError("cai")],
            )
        if n == 2:  # responde a partir do offset ERRADO
            return FakeResponse(
                206,
                {"content-range": f"bytes 0-{len(DADOS) - 1}/{len(DADOS)}"},
                _fatias(DADOS),
            )
        return servidor().handler(n, url, headers)

    cliente = FakeClient(handler)
    await hd.download_file(cliente, "http://x/f", destino, sleep=Dormidas())

    assert destino.read_bytes() == DADOS
    assert cliente.requests[2]["headers"]["Range"] == "bytes=0-"


async def test_mais_bytes_que_o_tamanho_declarado_e_refeito(tmp_path):
    destino = tmp_path / "faixa.bin"

    def handler(n, url, headers):
        if n == 1:  # promete 5000 e entrega 10 240
            return FakeResponse(200, {"content-length": "5000"}, _fatias(DADOS))
        return servidor().handler(n, url, headers)

    await hd.download_file(FakeClient(handler), "http://x/f", destino, sleep=Dormidas())

    assert destino.read_bytes() == DADOS


async def test_416_com_parcial_ja_completo_finaliza_sem_rebaixar(tmp_path):
    destino = tmp_path / "faixa.bin"
    # 1ª tentativa entrega TUDO mas a conexão quebra no fim; a 2ª recebe 416.
    cliente = servidor(falhas={1: ("cai", len(DADOS))})

    resultado = await hd.download_file(
        cliente, "http://x/f", destino, sleep=Dormidas()
    )

    assert destino.read_bytes() == DADOS
    assert resultado.attempts == 2
    assert resultado.size_verified is True


async def test_sem_content_length_informa_que_nao_verificou(tmp_path):
    destino = tmp_path / "faixa.bin"

    resultado = await hd.download_file(
        servidor(sem_length=True), "http://x/f", destino
    )

    assert destino.read_bytes() == DADOS
    assert resultado.size_verified is False  # chamador deve verificar o áudio


async def test_resposta_vazia_nunca_e_dada_como_concluida(tmp_path):
    """Bug original: total=0 fazia `baixado >= total` passar com 0 bytes."""
    destino = tmp_path / "faixa.bin"
    vazio = FakeClient(lambda n, u, h: FakeResponse(200, {}, []))

    with pytest.raises(hd.DownloadError):
        await hd.download_file(
            vazio, "http://x/f", destino, max_attempts=3, sleep=Dormidas()
        )

    assert not destino.exists()
    assert _sobras(tmp_path) == []


async def test_expected_size_usado_quando_servidor_nao_informa_tamanho(tmp_path):
    destino = tmp_path / "faixa.bin"
    cliente = servidor(sem_length=True, falhas={1: ("trunca", 5000)})

    resultado = await hd.download_file(
        cliente,
        "http://x/f",
        destino,
        expected_size=len(DADOS),
        sleep=Dormidas(),
    )

    assert destino.read_bytes() == DADOS
    assert resultado.attempts == 2
    assert resultado.size_verified is True


# ---------------------------------------------------------------------------
# Erros e retentativas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("codigo", [401, 403, 404, 451])
async def test_status_permanente_nao_e_retentado(tmp_path, codigo):
    cliente = FakeClient(lambda n, u, h: FakeResponse(codigo))

    with pytest.raises(hd.PermanentDownloadError):
        await hd.download_file(
            cliente, "http://x/f", tmp_path / "f.bin", sleep=Dormidas()
        )

    assert len(cliente.requests) == 1
    assert os.listdir(tmp_path) == []


async def test_503_e_retentado_com_backoff(tmp_path):
    dormidas = Dormidas()
    cliente = servidor(falhas={1: ("status", 503)})

    resultado = await hd.download_file(
        cliente, "http://x/f", tmp_path / "f.bin", sleep=dormidas
    )

    assert resultado.attempts == 2
    assert len(dormidas.atrasos) == 1 and dormidas.atrasos[0] >= 2.0


async def test_429_respeita_retry_after(tmp_path):
    dormidas = Dormidas()
    cliente = servidor(falhas={1: ("status", 429, {"retry-after": "7"})})

    await hd.download_file(cliente, "http://x/f", tmp_path / "f.bin", sleep=dormidas)

    assert dormidas.atrasos == [7.0]


async def test_tentativas_esgotadas_limpam_tudo_e_explicam_o_motivo(tmp_path):
    destino = tmp_path / "f.bin"
    sempre_cai = FakeClient(
        lambda n, u, h: FakeResponse(
            200, {"content-length": "100"}, [b"x" * 10, httpx.ReadError("cai")]
        )
    )

    with pytest.raises(hd.DownloadError) as info:
        await hd.download_file(
            sempre_cai, "http://x/f", destino, max_attempts=3, sleep=Dormidas()
        )

    assert "3 tentativas" in str(info.value)
    assert not destino.exists()
    assert _sobras(tmp_path) == []


async def test_expected_size_diferente_do_servidor_nao_e_retentado(tmp_path):
    cliente = servidor()

    with pytest.raises(hd.SizeMismatchError):
        await hd.download_file(
            cliente, "http://x/f", tmp_path / "f.bin", expected_size=999
        )

    assert len(cliente.requests) == 1
    assert _sobras(tmp_path) == []


async def test_checksum_correto_e_calculado(tmp_path):
    esperado = hashlib.sha256(DADOS).hexdigest()

    resultado = await hd.download_file(
        servidor(), "http://x/f", tmp_path / "f.bin", expected_sha256=esperado.upper()
    )

    assert resultado.sha256 == esperado


async def test_checksum_errado_nao_deixa_o_arquivo_final(tmp_path):
    destino = tmp_path / "f.bin"

    with pytest.raises(hd.ChecksumMismatchError):
        await hd.download_file(
            servidor(), "http://x/f", destino, expected_sha256="0" * 64
        )

    assert not destino.exists()
    assert _sobras(tmp_path) == []


async def test_url_pode_ser_funcao_chamada_a_cada_tentativa(tmp_path):
    chamadas = []

    def nova_url():
        chamadas.append(1)
        return f"http://x/f?token={len(chamadas)}"

    cliente = servidor(falhas={1: ("cai", 2000)})
    await hd.download_file(
        cliente, nova_url, tmp_path / "f.bin", sleep=Dormidas()
    )

    assert [r["url"] for r in cliente.requests] == [
        "http://x/f?token=1",
        "http://x/f?token=2",
    ]


async def test_url_pode_ser_corrotina(tmp_path):
    async def url_async():
        return "http://x/assinada"

    cliente = servidor()
    await hd.download_file(cliente, url_async, tmp_path / "f.bin")

    assert cliente.requests[0]["url"] == "http://x/assinada"


async def test_abort_interrompe_e_limpa(tmp_path):
    destino = tmp_path / "f.bin"
    chamadas = {"n": 0}

    def abortar():
        chamadas["n"] += 1
        return chamadas["n"] > 3  # deixa passar a 1ª tentativa, aborta no meio

    with pytest.raises(hd.DownloadAborted):
        await hd.download_file(
            servidor(), "http://x/f", destino, should_abort=abortar
        )

    assert not destino.exists()
    assert _sobras(tmp_path) == []


async def test_erro_de_disco_nao_e_retentado(tmp_path):
    arquivo = tmp_path / "eu_sou_um_arquivo"
    arquivo.write_text("x")
    cliente = servidor()

    with pytest.raises(OSError):
        await hd.download_file(cliente, "http://x/f", arquivo / "sub" / "f.bin")

    assert cliente.requests == []


async def test_max_attempts_invalido(tmp_path):
    with pytest.raises(ValueError):
        await hd.download_file(
            servidor(), "http://x/f", tmp_path / "f.bin", max_attempts=0
        )


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


async def test_make_client_cai_para_http11_se_h2_nao_estiver_instalado():
    cliente = hd.make_client(http2=True)
    try:
        assert isinstance(cliente, httpx.AsyncClient)
    finally:
        await cliente.aclose()
