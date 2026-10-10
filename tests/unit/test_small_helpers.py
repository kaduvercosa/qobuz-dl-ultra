"""Helpers pequenos que ficaram sem nenhuma chamada direta nos testes:
`sentinel` (timestamp, contenção de caminho, validação de inteiros),
`library_cmd` (caminho do banco, rótulos) e `search_prompt` (aviso único).
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qobuz_dl import library_cmd, search_prompt, sentinel

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# sentinel
# ---------------------------------------------------------------------------
class TestUtcNowIso:
    def test_tem_offset_utc_e_precisao_de_segundos(self):
        texto = sentinel.utc_now_iso()
        momento = datetime.fromisoformat(texto)
        assert momento.utcoffset() == timedelta(0)
        assert momento.microsecond == 0
        assert texto.endswith("+00:00")

    def test_e_atual(self):
        agora = datetime.now(timezone.utc)
        assert abs(datetime.fromisoformat(sentinel.utc_now_iso()) - agora) < timedelta(
            seconds=5
        )

    def test_ordena_como_texto(self):
        a = "2026-10-09T10:00:00+00:00"
        b = "2026-10-09T10:00:01+00:00"
        assert a < b


class TestInside:
    def test_filho_direto_e_profundo(self, tmp_path):
        assert sentinel._inside(tmp_path / "a", tmp_path)
        assert sentinel._inside(tmp_path / "a" / "b" / "c.flac", tmp_path)

    def test_a_propria_raiz(self, tmp_path):
        assert sentinel._inside(tmp_path, tmp_path)

    def test_irmao_e_pai_estao_fora(self, tmp_path):
        raiz = tmp_path / "lib"
        assert not sentinel._inside(tmp_path / "outra", raiz)
        assert not sentinel._inside(tmp_path, raiz)

    def test_prefixo_de_nome_nao_engana(self, tmp_path):
        # "/lib2" começa com "/lib" como texto, mas NÃO está dentro dela.
        assert not sentinel._inside(tmp_path / "lib2" / "x", tmp_path / "lib")

    def test_nao_resolve_dotdot_sozinho(self, tmp_path):
        # A função compara caminhos como estão: quem a chama deve resolver.
        assert sentinel._inside(tmp_path / "lib" / ".." / "fora", tmp_path / "lib")
        assert not sentinel._inside(
            (tmp_path / "lib" / ".." / "fora").resolve(), tmp_path / "lib"
        )


class TestPositiveInt:
    @pytest.mark.parametrize(
        "payload, esperado",
        [
            ({"n": 5}, 5),
            ({"n": "7"}, 7),
            ({"n": 3.0}, 3),
            ({"n": " 9 "}, 9),
        ],
    )
    def test_valores_validos(self, payload, esperado):
        assert sentinel._positive_int(payload, "n") == esperado

    @pytest.mark.parametrize("payload", [{}, {"n": None}, {"outro": 1}])
    def test_ausente_ou_nulo_devolve_none(self, payload):
        assert sentinel._positive_int(payload, "n") is None

    @pytest.mark.parametrize(
        "valor", [0, -1, "0", "-3", "abc", "", "1.5", [], {}, True, False, float("nan")]
    )
    def test_invalidos_levantam(self, valor):
        with pytest.raises(sentinel.SentinelValidationError, match="n inválido"):
            sentinel._positive_int({"n": valor}, "n")

    def test_mensagem_cita_a_chave(self):
        with pytest.raises(
            sentinel.SentinelValidationError, match="bit_depth inválido"
        ):
            sentinel._positive_int({"bit_depth": "x"}, "bit_depth")


# ---------------------------------------------------------------------------
# library_cmd
# ---------------------------------------------------------------------------
class TestLibraryDbPath:
    def test_caminho_explicito(self):
        assert library_cmd.library_db_path("/cfg/qobuz-dl") == str(
            Path("/cfg/qobuz-dl") / "library.db"
        )

    def test_usa_a_pasta_do_config_por_padrao(self, monkeypatch):
        monkeypatch.setattr(
            "qobuz_dl.utils.get_config_paths", lambda: {"config_path": "/meu/config"}
        )
        assert library_cmd.library_db_path() == str(Path("/meu/config") / "library.db")

    def test_fica_ao_lado_do_config_ini_real(self):
        import os

        caminho = library_cmd.library_db_path()
        assert os.path.basename(caminho) == "library.db"
        assert os.path.basename(os.path.dirname(caminho)) == "qobuz-dl"


def test_label():
    assert library_cmd._label({"artist": "Daft Punk", "title": "Discovery"}) == (
        "Daft Punk - Discovery"
    )


class TestFmtQuality:
    @pytest.mark.parametrize(
        "album, esperado",
        [
            ({"bit_depth": 24, "sample_rate": 96.0}, "24bit/96kHz"),
            ({"bit_depth": 16, "sample_rate": 44.1}, "16bit/44.1kHz"),
            ({"bit_depth": 24, "sample_rate": 192}, "24bit/192kHz"),
            ({"bit_depth": 16}, "16bit"),
            ({"bit_depth": 16, "sample_rate": None}, "16bit"),
            ({"bit_depth": 16, "sample_rate": 0}, "16bit"),
            ({}, ""),
            ({"bit_depth": None, "sample_rate": 44.1}, ""),
            ({"bit_depth": 0}, ""),
        ],
    )
    def test_formatos(self, album, esperado):
        assert library_cmd._fmt_quality(album) == esperado

    def test_sample_rate_em_texto(self):
        assert library_cmd._fmt_quality({"bit_depth": 24, "sample_rate": "88.2"}) == (
            "24bit/88.2kHz"
        )


# ---------------------------------------------------------------------------
# search_prompt
# ---------------------------------------------------------------------------
class TestWarnOnce:
    def test_avisa_so_na_primeira_vez(self, monkeypatch, caplog):
        monkeypatch.setattr(search_prompt, "_warned", False)
        mensagens = []
        monkeypatch.setattr(
            search_prompt.logger, "warning", lambda msg, *a, **k: mensagens.append(msg)
        )
        search_prompt._warn_once("primeira")
        search_prompt._warn_once("segunda")
        search_prompt._warn_once("terceira")
        assert mensagens == ["primeira"]
        assert search_prompt._warned is True

    def test_nao_avisa_se_ja_avisou(self, monkeypatch):
        monkeypatch.setattr(search_prompt, "_warned", True)
        mensagens = []
        monkeypatch.setattr(
            search_prompt.logger, "warning", lambda msg, *a, **k: mensagens.append(msg)
        )
        search_prompt._warn_once("x")
        assert mensagens == []

    def test_o_logger_e_do_modulo(self):
        assert isinstance(search_prompt.logger, logging.Logger)
        assert search_prompt.logger.name == "qobuz_dl.search_prompt"
