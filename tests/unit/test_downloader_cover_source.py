"""Origem da capa nos downloads (`Capa: Apple/iTunes` / `Capa: Qobuz`).

Dois defeitos reais cobertos aqui:

1. `_process_track` (método extraído de `download_release`) usava
   `cover_source` sem que ela existisse no escopo -> `NameError` em TODA faixa
   de álbum baixada. `download_track` tinha o mesmo problema
   (`UnboundLocalError`) quando não havia capa para baixar.
2. A origem gravada nas tags era fixa em "Qobuz", mesmo com a capa da Apple
   embutida. Agora `_get_cover_and_embed` devolve a origem real.

Nada toca rede nem disco real além de `tmp_path`.
"""

import asyncio
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader
from qobuz_dl.downloader import Download

pytestmark = pytest.mark.unit

APPLE = "Apple/iTunes"
QOBUZ = "Qobuz"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _ArquivoAssincrono:
    def __init__(self, caminho):
        self.caminho = caminho
        self._f = None

    async def __aenter__(self):
        self._f = open(self.caminho, "wb")
        return self

    async def __aexit__(self, *a):
        self._f.close()

    async def write(self, dados):
        self._f.write(dados)


@pytest.fixture
def capa(monkeypatch):
    """Isola `_get_cover_and_embed`: Apple, Qobuz e gravação em disco."""
    estado = {
        "apple": b"APPLE",
        "qobuz": b"QOBUZ",
        "chamadas_apple": 0,
        "chamadas_qobuz": 0,
        "url": lambda item, tamanho: f"https://qobuz/{tamanho}.jpg",
    }

    async def fake_apple(session, **kw):
        estado["chamadas_apple"] += 1
        return estado["apple"]

    async def fake_qobuz(item, tamanho, session):
        estado["chamadas_qobuz"] += 1
        return estado["qobuz"]

    monkeypatch.setattr(downloader, "_try_apple_cover_bytes", fake_apple)
    monkeypatch.setattr(downloader, "_fetch_qobuz_cover_bytes", fake_qobuz)
    monkeypatch.setattr(
        downloader, "_resolve_art_url", lambda item, t: estado["url"](item, t)
    )
    monkeypatch.setattr(
        downloader, "aiofiles", SimpleNamespace(open=lambda c, m: _ArquivoAssincrono(c))
    )
    downloader.abort_event.clear()
    downloader._COVER_SOURCES.clear()
    yield estado
    downloader.abort_event.clear()
    downloader._COVER_SOURCES.clear()


async def _obter(
    tmp_path,
    *,
    salvar=True,
    embutir=True,
    embed_name=".embed.jpg",
    saved_art_size="large",
    embedded_art_size="large",
):
    return await downloader._get_cover_and_embed(
        {"image": {}},
        str(tmp_path),
        save_cover=salvar,
        embed_art=embutir,
        saved_name="cover.jpg",
        embed_name=embed_name,
        saved_art_size=saved_art_size,
        embedded_art_size=embedded_art_size,
        artist="A",
        album="B",
    )


# ---------------------------------------------------------------------------
# Rótulos
# ---------------------------------------------------------------------------
def test_rotulos_sao_os_mesmos_do_comando_tags():
    assert downloader.COVER_SOURCE_APPLE_LABEL == APPLE
    assert downloader.COVER_SOURCE_QOBUZ_LABEL == QOBUZ


# ---------------------------------------------------------------------------
# Origem devolvida por _get_cover_and_embed
# ---------------------------------------------------------------------------
class TestOrigemDevolvida:
    async def test_apple_grava_as_duas_capas_e_informa_apple(self, capa, tmp_path):
        assert await _obter(tmp_path) == APPLE
        assert (tmp_path / "cover.jpg").read_bytes() == b"APPLE"
        assert (tmp_path / ".embed.jpg").read_bytes() == b"APPLE"
        assert capa["chamadas_qobuz"] == 0

    async def test_apple_so_para_embed(self, capa, tmp_path):
        assert await _obter(tmp_path, salvar=False) == APPLE
        assert not (tmp_path / "cover.jpg").exists()

    async def test_sem_apple_cai_para_qobuz(self, capa, tmp_path):
        capa["apple"] = None
        assert await _obter(tmp_path) == QOBUZ
        assert (tmp_path / "cover.jpg").read_bytes() == b"QOBUZ"

    async def test_qobuz_reaproveita_a_capa_salva_no_embed(self, capa, tmp_path):
        capa["apple"] = None
        assert await _obter(tmp_path) == QOBUZ
        # Mesma URL nos dois tamanhos: baixa uma vez e copia para o embed.
        assert capa["chamadas_qobuz"] == 1
        assert (tmp_path / ".embed.jpg").read_bytes() == b"QOBUZ"

    async def test_qobuz_com_tamanhos_diferentes_baixa_duas_vezes(self, capa, tmp_path):
        capa["apple"] = None
        assert (
            await _obter(tmp_path, saved_art_size="max", embedded_art_size="small")
            == QOBUZ
        )
        assert capa["chamadas_qobuz"] == 2

    async def test_nenhuma_fonte_devolve_none_e_nao_grava_nada(self, capa, tmp_path):
        capa["apple"] = None
        capa["qobuz"] = None
        assert await _obter(tmp_path) is None
        assert list(tmp_path.iterdir()) == []

    async def test_so_embed_com_qobuz_falhando_devolve_none(self, capa, tmp_path):
        capa["apple"] = None
        capa["qobuz"] = None
        assert await _obter(tmp_path, salvar=False) is None

    async def test_nada_para_fazer(self, capa, tmp_path):
        assert await _obter(tmp_path, salvar=False, embutir=False) is None
        assert capa["chamadas_apple"] == 0

    async def test_abort_devolve_none_sem_buscar(self, capa, tmp_path):
        downloader.abort_event.set()
        assert await _obter(tmp_path) is None
        assert capa["chamadas_apple"] == 0

    async def test_qobuz_so_salva_sem_embed(self, capa, tmp_path):
        capa["apple"] = None
        assert await _obter(tmp_path, embutir=False, embed_name="") == QOBUZ


class TestArquivosJaExistentes:
    async def test_segunda_faixa_do_album_lembra_a_origem(self, capa, tmp_path):
        assert await _obter(tmp_path) == APPLE
        capa["chamadas_apple"] = 0
        # Os arquivos já existem: não busca de novo, mas ainda sabe a origem.
        assert await _obter(tmp_path) == APPLE
        assert capa["chamadas_apple"] == 0

    async def test_origem_qobuz_tambem_e_lembrada(self, capa, tmp_path):
        capa["apple"] = None
        await _obter(tmp_path)
        assert await _obter(tmp_path) == QOBUZ

    async def test_arquivo_de_execucao_anterior_tem_origem_desconhecida(
        self, capa, tmp_path
    ):
        (tmp_path / "cover.jpg").write_bytes(b"velho")
        (tmp_path / ".embed.jpg").write_bytes(b"velho")
        # Nada a afirmar: melhor sem linha "Capa:" do que uma origem errada.
        assert await _obter(tmp_path) is None
        assert capa["chamadas_apple"] == 0

    async def test_pastas_diferentes_nao_misturam_origens(self, capa, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert await _obter(a) == APPLE
        capa["apple"] = None
        assert await _obter(b) == QOBUZ
        assert await _obter(a) == APPLE

    async def test_so_a_capa_salva_falta_e_a_embed_existe(self, capa, tmp_path):
        (tmp_path / ".embed.jpg").write_bytes(b"velho")
        # Falta só a salva: busca de novo e informa a origem do que gravou.
        assert await _obter(tmp_path) == APPLE
        assert (tmp_path / "cover.jpg").read_bytes() == b"APPLE"
        assert (tmp_path / ".embed.jpg").read_bytes() == b"velho"


# ---------------------------------------------------------------------------
# cover_source chega até _download_and_tag (era NameError)
# ---------------------------------------------------------------------------
def _self(**kw):
    s = SimpleNamespace(quality=6, client=SimpleNamespace())
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _kwargs(tmp_path, report, **extra):
    base = dict(
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=asyncio.Semaphore(1),
        report_track=report,
    )
    base.update(extra)
    return base


@pytest.fixture
def faixa_ok():
    """`self` falso com stream válido; guarda o que `_download_and_tag` recebe."""
    recebido = {}

    async def report(i, t_num, status, motivo="", letras=None):
        recebido["status"] = status

    async def get_track_url(track_id, fmt_id):
        return {"sampling_rate": 44.1}

    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        recebido["kwargs"] = kwargs
        return True

    s = _self(
        client=SimpleNamespace(get_track_url=get_track_url),
        _download_and_tag=download_and_tag,
    )
    downloader.abort_event.clear()
    yield s, report, recebido
    downloader.abort_event.clear()


FAIXA = {"id": 1, "track_number": 1, "title": "Musica", "streamable": True}


class TestProcessTrackRepassaCoverSource:
    async def test_sem_cover_source_nao_levanta_e_repassa_none(
        self, faixa_ok, tmp_path
    ):
        s, report, recebido = faixa_ok
        # Regressão: antes disto era NameError ("cover_source" não definida).
        assert (
            await Download._process_track(s, 0, FAIXA, **_kwargs(tmp_path, report))
            is True
        )
        assert recebido["kwargs"]["cover_source"] is None
        assert recebido["status"] == "ok"

    @pytest.mark.parametrize("origem", [APPLE, QOBUZ])
    async def test_repassa_a_origem_recebida(self, faixa_ok, tmp_path, origem):
        s, report, recebido = faixa_ok
        await Download._process_track(
            s, 0, FAIXA, **_kwargs(tmp_path, report, cover_source=origem)
        )
        assert recebido["kwargs"]["cover_source"] == origem
