"""Testes de helpers pequenos e de baixo risco do downloader."""

import json
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


def test_build_letras_report_empty():
    assert downloader._build_letras_report(None, None, None) == {}


def test_build_letras_report_success_bilingual():
    result = downloader._build_letras_report(
        {
            "success": True,
            "language": "en+pt",
            "synchronized": True,
            "bilingual": True,
            "embedded": True,
            "saved_external": True,
            "source": "lrclib",
        },
        "pt",
        {"translated": True},
    )

    assert result == {
        "situacao": "sucesso",
        "sincronizada": True,
        "bilingue": True,
        "idioma_original": "en",
        "traducao_disponivel": True,
        "fonte": "lrclib",
        "destino": "metadata + .lrc/.txt",
        "observacao": "",
    }


def test_build_letras_report_failure():
    result = downloader._build_letras_report(
        {"error": "not found", "language": "unknown"},
        "pt",
        None,
    )

    assert result["situacao"] == "falha"
    assert result["idioma_original"] == ""
    assert result["observacao"] == "not found"
    assert result["traducao_disponivel"] is False


def test_emit_progress_json_desligado(capsys):
    downloader.emit_progress_json(SimpleNamespace(progress_json=False), "start", id="x")
    assert capsys.readouterr().out == ""


def test_emit_progress_json_ligado(capsys):
    downloader.emit_progress_json(
        SimpleNamespace(progress_json=True),
        "finish",
        id="track-1",
        status="ok",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "finish"
    assert payload["id"] == "track-1"
    assert payload["status"] == "ok"
    assert isinstance(payload["ts"], float)


def test_safe_print_junta_args_com_espaco_e_usa_ui_emit(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        downloader.ui, "emit", lambda text="", end="\n": chamadas.append((text, end))
    )

    downloader.safe_print("a", 1, "b")
    downloader.safe_print("x", end="")

    assert chamadas == [("a 1 b", "\n"), ("x", "")]


def test_get_description_inclui_bit_depth_e_sampling_rate():
    assert (
        downloader._get_description({"bit_depth": 24, "sampling_rate": 96}, "Musica")
        == "Musica [24/96]"
    )


def test_get_description_com_multiple_adiciona_prefixo_de_cd():
    assert downloader._get_description({}, "Musica", multiple=2) == "[CD 2] Musica [/]"


class TestResolveArtUrl:
    def test_tamanho_reconhecido_substitui_600_pelo_pedido(self):
        assert (
            downloader._resolve_art_url("https://x.com/img_600.jpg", "300")
            == "https://x.com/img_300.jpg"
        )

    def test_og_quality_forca_org_independente_do_art_size_pedido(self):
        assert (
            downloader._resolve_art_url(
                "https://x.com/img_600.jpg", "150", og_quality=True
            )
            == "https://x.com/img_org.jpg"
        )

    def test_tamanho_nao_reconhecido_devolve_url_original(self):
        # "999" não está na lista de tamanhos válidos -- sem substituição,
        # devolve exatamente o que recebeu.
        assert (
            downloader._resolve_art_url("https://x.com/img_600.jpg", "999")
            == "https://x.com/img_600.jpg"
        )


class TestCleanEmbedArt:
    def test_remove_o_arquivo_de_capa_embutida_quando_existe(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
        embed_path = tmp_path / downloader.EMB_COVER_NAME
        embed_path.write_bytes(b"fake-jpg")

        downloader._clean_embed_art(str(tmp_path))

        assert not embed_path.exists()

    def test_arquivo_ausente_nao_faz_nada_nem_quebra(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
        downloader._clean_embed_art(str(tmp_path))  # não pode levantar

    def test_oserror_no_remove_e_engolido(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
        embed_path = tmp_path / downloader.EMB_COVER_NAME
        embed_path.write_bytes(b"fake-jpg")

        def _remove_falha(caminho):
            raise OSError("arquivo travado por outro processo")

        monkeypatch.setattr(downloader.os, "remove", _remove_falha)

        downloader._clean_embed_art(str(tmp_path))  # não pode levantar


class TestGetExtra:
    """_get_extra() nunca tinha teste dedicado -- os outros arquivos só
    mockam ela como dependência de download_and_tag(), nunca exercitam o
    corpo de verdade. Evita depender de httpx real mockando
    tqdm_download/_resolve_art_url diretamente (mesmo padrão usado em
    test_downloader_*.py pro resto do módulo)."""

    async def test_abort_event_ligado_nao_baixa_nada(self, monkeypatch, tmp_path):
        chamadas = []
        monkeypatch.setattr(
            downloader, "tqdm_download", lambda *a, **k: chamadas.append(1)
        )
        downloader.abort_event.set()
        try:
            resultado = await downloader._get_extra("url", str(tmp_path))
        finally:
            downloader.abort_event.clear()  # estado global -- nunca vazar pro resto da suíte

        assert resultado is None
        assert chamadas == []

    async def test_arquivo_ja_baixado_pula_sem_chamar_tqdm_download(
        self, monkeypatch, tmp_path
    ):
        (tmp_path / "cover.jpg").write_bytes(b"ja existe")
        avisos = []
        monkeypatch.setattr(downloader.ui, "skip", avisos.append)
        chamadas = []
        monkeypatch.setattr(
            downloader, "tqdm_download", lambda *a, **k: chamadas.append(1)
        )

        await downloader._get_extra("url", str(tmp_path), extra="cover.jpg")

        assert chamadas == []
        assert "Já baixado" in avisos[0]

    async def test_resolve_a_url_e_chama_tqdm_download_com_o_arquivo_certo(
        self, monkeypatch, tmp_path
    ):
        capturado = {}

        async def fake_tqdm(item, extra_file, extra, **kwargs):
            capturado["item"] = item
            capturado["extra_file"] = extra_file

        monkeypatch.setattr(downloader, "tqdm_download", fake_tqdm)
        monkeypatch.setattr(
            downloader,
            "_resolve_art_url",
            lambda item, art_size, og_quality=False: item.replace("_600.", "_300."),
        )

        await downloader._get_extra(
            "https://x.com/img_600.jpg",
            str(tmp_path),
            extra="cover.jpg",
            art_size="300",
        )

        assert capturado["item"] == "https://x.com/img_300.jpg"
        assert capturado["extra_file"] == str(tmp_path / "cover.jpg")

    async def test_falha_no_download_e_engolida_com_aviso(self, monkeypatch, tmp_path):
        async def fake_tqdm_falha(*a, **k):
            raise RuntimeError("conexao recusada")

        monkeypatch.setattr(downloader, "tqdm_download", fake_tqdm_falha)
        avisos = []
        monkeypatch.setattr(downloader.ui, "skip", avisos.append)

        await downloader._get_extra(
            "https://x.com/img.jpg", str(tmp_path), extra="cover2.jpg", label="capa"
        )

        assert "conexao recusada" in avisos[0]


class TestTryAppleCoverBytes:
    """Testa o controle de fluxo de _try_apple_cover_bytes mockando
    get_apple_hq_cover/_download_bytes_with_limit -- não precisa de
    httpx real pra cobrir os 4 caminhos (sem artist/album, busca
    falhando, primeira resolução já serve, nenhuma resolução serve)."""

    async def test_sem_artist_ou_album_nao_busca_nada(self, monkeypatch):
        chamadas = []

        async def fake_get(**kwargs):
            chamadas.append(kwargs)
            return "https://apple/100x100bb.jpg"

        monkeypatch.setattr(downloader, "get_apple_hq_cover", fake_get)

        resultado = await downloader._try_apple_cover_bytes(
            None, artist=None, album="Album X"
        )

        assert resultado is None
        assert chamadas == []

    async def test_busca_na_apple_falhando_cai_pro_qobuz(self, monkeypatch):
        async def fake_get_falha(**kwargs):
            raise RuntimeError("timeout na Apple")

        monkeypatch.setattr(downloader, "get_apple_hq_cover", fake_get_falha)

        resultado = await downloader._try_apple_cover_bytes(
            None, artist="Artista", album="Album"
        )

        assert resultado is None

    async def test_primeira_resolucao_que_couber_e_usada_sem_tentar_as_outras(
        self, monkeypatch
    ):
        async def fake_get(**kwargs):
            return "https://apple/100x100bb.jpg"

        tentativas = []

        async def fake_download_ok(url, session, max_bytes, headers=None):
            tentativas.append(url)
            return b"bytes-da-capa"

        monkeypatch.setattr(downloader, "get_apple_hq_cover", fake_get)
        monkeypatch.setattr(downloader, "_download_bytes_with_limit", fake_download_ok)

        resultado = await downloader._try_apple_cover_bytes(
            None, artist="Artista", album="Album"
        )

        assert resultado == b"bytes-da-capa"
        assert len(tentativas) == 1

    async def test_nenhuma_resolucao_cabe_no_limite_devolve_none(self, monkeypatch):
        async def fake_get(**kwargs):
            return "https://apple/100x100bb.jpg"

        tentativas = []

        async def fake_download_falha(url, session, max_bytes, headers=None):
            tentativas.append(url)
            return None

        monkeypatch.setattr(downloader, "get_apple_hq_cover", fake_get)
        monkeypatch.setattr(
            downloader, "_download_bytes_with_limit", fake_download_falha
        )

        resultado = await downloader._try_apple_cover_bytes(
            None, artist="Artista", album="Album"
        )

        assert resultado is None
        assert len(tentativas) == len(downloader._APPLE_COVER_SIZES)
