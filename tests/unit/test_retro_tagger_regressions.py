"""Regressões de retro_tagger: cor do relatório e pasta padrão."""

from types import SimpleNamespace

import pytest

from qobuz_dl import retro_tagger as rt

pytestmark = pytest.mark.unit


class _Engine:
    def __init__(self, *a, **k):
        pass

    def fetch_and_inject(self, **kwargs):
        return {"success": True}

    def close(self):
        pass


async def test_status_sem_alteracao_usa_prefixo_neutro(tmp_path, monkeypatch):
    saida = []
    (tmp_path / "a.flac").write_bytes(b"x")
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": saida.append(t))
    monkeypatch.setattr(rt, "LyricsEngine", _Engine)
    monkeypatch.setattr(rt, "extract_track_id", lambda p: None)
    monkeypatch.setattr(rt, "FLAC", lambda p: {})
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        lambda p: {
            "has_lyrics": True,
            "is_bilingual": False,
            "content": "x",
            "lrc_exists": True,
            "language": "pt",
        },
    )
    await rt.process_retroactive_lyrics_async(
        str(tmp_path), None, settings=SimpleNamespace()
    )
    texto = "\n".join(saida)
    assert "SEM ALTERAÇÃO" in texto
    assert "[-]" in texto
    assert "[!]" not in texto


@pytest.fixture
def espiao(monkeypatch):
    chamadas = []

    async def fake(**kwargs):
        chamadas.append(kwargs)

    monkeypatch.setattr(rt, "process_retroactive_lyrics_async", fake)
    monkeypatch.setattr(rt.ui, "emit", lambda t="", end="\n": None)
    return chamadas


class _Raw:
    def __init__(self, valor=None, erro=None):
        self.valor, self.erro = valor, erro

    def get(self, secao, chave, fallback=None):
        if self.erro:
            raise self.erro
        return self.valor


async def test_pasta_vem_do_config_ini_quando_default_folder_ausente(tmp_path, espiao):
    settings = SimpleNamespace(default_folder="", raw_settings=_Raw(str(tmp_path)))
    await rt.inject_lyrics_retroactively(settings=settings)
    assert espiao[0]["directory_path"] == str(tmp_path)


async def test_pasta_padrao_quando_config_falha(tmp_path, espiao, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "QobuzDownloads").mkdir()
    settings = SimpleNamespace(
        default_folder="", raw_settings=_Raw(erro=RuntimeError("sem config"))
    )
    await rt.inject_lyrics_retroactively(settings=settings)
    assert espiao[0]["directory_path"] == "QobuzDownloads"
