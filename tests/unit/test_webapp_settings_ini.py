"""Testes da leitura/gravação das preferências da GUI no config.ini."""

from __future__ import annotations

import pytest

from qobuz_dl import webapp
from qobuz_dl.webapp import GuiService, SettingsRequest


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    config_file = config_dir / "config.ini"
    monkeypatch.setattr(
        webapp,
        "get_config_paths",
        lambda: {
            "config_path": str(config_dir),
            "config_file": str(config_file),
            "qobuz_db": str(config_dir / "downloads.db"),
        },
    )
    return config_file


def load(cfg_file, body: str | None, header: str = "[DEFAULT]") -> dict:
    if body is not None:
        cfg_file.write_text(f"{header}\n{body}\n", encoding="utf-8")
    return GuiService(demo=True).local_settings


def test_sem_arquivo_usa_padroes(cfg):
    s = load(cfg, None)
    assert s["max_workers"] == 1
    assert s["segment_workers"] == 4
    assert s["quality"] is None
    assert s["embed_art"] is True
    assert s["lrc_files"] is True
    assert s["m3u"] is False


def test_ini_corrompido_usa_padroes(cfg):
    cfg.write_text("isto nao e um ini valido\n", encoding="utf-8")
    s = GuiService(demo=True).local_settings
    assert s["max_workers"] == 1
    assert s["embed_art"] is True


@pytest.mark.parametrize(
    ("ini_line", "gui_key", "esperado"),
    [
        ("no_lrc_files = true", "lrc_files", False),
        ("no_lrc_files = false", "lrc_files", True),
        ("no_credits = true", "credits", False),
        ("no_credits = false", "credits", True),
        ("no_m3u = true", "m3u", False),
        ("no_m3u = false", "m3u", True),
        ("no_fallback = true", "quality_fallback", False),
        ("no_fallback = false", "quality_fallback", True),
    ],
)
def test_flags_invertidas(cfg, ini_line, gui_key, esperado):
    assert load(cfg, ini_line)[gui_key] is esperado


@pytest.mark.parametrize(
    ("ini_line", "gui_key", "esperado"),
    [
        ("embed_art = false", "embed_art", False),
        ("embed_art = yes", "embed_art", True),
        ("playlist_as_albums = true", "playlist_as_albums", True),
        ("verify_after_download = 1", "verify_after_download", True),
        ("no_cover = on", "no_cover", True),
        ("smart_discography = true", "smart_discography", True),
        ("multi_value_tags = true", "multi_value_tags", True),
    ],
)
def test_flags_diretas(cfg, ini_line, gui_key, esperado):
    assert load(cfg, ini_line)[gui_key] is esperado


def test_valor_booleano_vazio_e_ignorado(cfg):
    assert load(cfg, "embed_art =")["embed_art"] is True


def test_embed_lyrics_legado_controla_fetch_lyrics(cfg):
    assert load(cfg, "embed_lyrics = false")["fetch_lyrics"] is False


def test_fetch_lyrics_explicito_tem_prioridade_sobre_embed_lyrics(cfg):
    s = load(cfg, "fetch_lyrics = true\nembed_lyrics = false")
    assert s["fetch_lyrics"] is True


def test_strings_e_valores_em_branco(cfg):
    s = load(cfg, "embedded_art_size = 600\nsaved_art_size =   ")
    assert s["embedded_art_size"] == "600"
    assert s["saved_art_size"] == "org"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("99", 16), ("0", 1), ("-3", 1), ("8", 8), ("abc", 1)],
)
def test_max_workers_limitado(cfg, valor, esperado):
    assert load(cfg, f"max_workers = {valor}")["max_workers"] == esperado


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("99", 16), ("1", 2), ("10", 10), ("abc", 4)],
)
def test_segment_workers_limitado(cfg, valor, esperado):
    assert load(cfg, f"segment_workers = {valor}")["segment_workers"] == esperado


def test_directory_tem_prioridade_sobre_default_folder(cfg):
    s = load(cfg, "directory = /a\ndefault_folder = /b")
    assert s["directory"] == "/a"


def test_default_folder_como_alternativa(cfg):
    assert load(cfg, "default_folder = /b")["directory"] == "/b"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("5", 5), ("6", 6), ("7", 7), ("27", 27), ("99", None), ("abc", None)],
)
def test_default_quality_valida(cfg, valor, esperado):
    assert load(cfg, f"default_quality = {valor}")["quality"] == esperado


def test_secao_qobuz_e_usada_quando_existe(cfg):
    s = load(cfg, "max_workers = 5", header="[qobuz]")
    assert s["max_workers"] == 5


def _payload(directory, **overrides) -> SettingsRequest:
    data = {
        "directory": str(directory),
        "quality": 7,
        "embed_art": False,
        "fetch_lyrics": False,
        "lrc_files": False,
        "credits": False,
        "m3u": True,
        "quality_fallback": False,
        "playlist_as_albums": True,
        "verify_after_download": True,
        "max_workers": 6,
        "segment_workers": 10,
        "embedded_art_size": "600",
        "saved_art_size": "300",
    }
    data.update(overrides)
    return SettingsRequest(**data)


def test_salvar_e_recarregar_preserva_valores(cfg, tmp_path):
    musica = tmp_path / "Music"
    service = GuiService(demo=True)
    service.save_settings(_payload(musica))

    s = GuiService(demo=True).local_settings
    assert s["directory"] == str(musica)
    assert s["quality"] == 7
    assert s["embed_art"] is False
    assert s["lrc_files"] is False
    assert s["credits"] is False
    assert s["m3u"] is True
    assert s["quality_fallback"] is False
    assert s["playlist_as_albums"] is True
    assert s["verify_after_download"] is True
    assert s["max_workers"] == 6
    assert s["segment_workers"] == 10
    assert s["embedded_art_size"] == "600"
    assert s["saved_art_size"] == "300"


def test_salvar_cria_a_pasta_de_musica(cfg, tmp_path):
    musica = tmp_path / "nova" / "pasta"
    GuiService(demo=True).save_settings(_payload(musica))
    assert musica.is_dir()


def test_qualidade_fora_da_lista_e_rejeitada(cfg, tmp_path):
    service = GuiService(demo=True)
    with pytest.raises(ValueError, match="Qualidade"):
        service.save_settings(_payload(tmp_path / "m", quality=8))
    assert not cfg.exists()
