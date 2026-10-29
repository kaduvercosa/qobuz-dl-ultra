"""Lote 3 (integração): --dry-run, diário e progresso ligados ao retro_tagger."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qobuz_dl import retro_tagger as rt
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.lyrics_engine import LyricsCandidate, LyricsEngine
from qobuz_dl.lyrics_progress import LyricsProgress
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------- o motor
def _engine():
    eng = LyricsEngine(session=MagicMock())
    candidato = LyricsCandidate("[00:01.000]hello", "Fake", True, language="en")
    eng._provider_fake = lambda consulta: candidato
    eng.PROVIDERS = ("_provider_fake",)
    eng._inject_metadata = MagicMock(return_value=True)
    eng._save_lrc_file = MagicMock(return_value=True)
    return eng


def _buscar(eng, **extra):
    return eng.fetch_and_inject(
        file_path="/m/a.flac", artist="A", track="T", album="Al", quiet=True, **extra
    )


def test_motor_em_simulacao_acha_a_letra_mas_nao_grava():
    eng = _engine()
    res = _buscar(eng, dry_run=True)
    assert res["success"] and res["source"] == "Fake" and res["language"] == "en"
    assert res["embedded"] and res["saved_external"]
    eng._inject_metadata.assert_not_called()
    eng._save_lrc_file.assert_not_called()


def test_motor_sem_simulacao_continua_gravando():
    eng = _engine()
    assert _buscar(eng)["success"]
    eng._inject_metadata.assert_called_once()
    eng._save_lrc_file.assert_called_once()


# ------------------------------------------------------- o comando `lyrics`
class _Engine:
    def __init__(self):
        self.chamadas = []

    def fetch_and_inject(self, **kw):
        self.chamadas.append(kw)
        return {"success": True}

    def close(self):
        pass


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    raiz = tmp_path / "musica"
    raiz.mkdir()
    (raiz / "f1.flac").write_bytes(b"x")
    monkeypatch.setattr(rt.ui, "emit", lambda texto="", end="\n": None)
    monkeypatch.setattr(rt, "extract_track_id", lambda p: "100")
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        lambda p: {
            "has_lyrics": False,
            "is_bilingual": False,
            "is_synced": False,
            "content": "",
            "lrc_exists": False,
            "language": None,
        },
    )
    monkeypatch.setattr(rt, "FLAC", lambda p: {"TITLE": ["f1"], "ARTIST": ["A"]})
    consultas = []

    async def buscar(cli, tid, language=None):
        consultas.append(language)
        return {"original": {"lang": "en", "lines": [{"start": 1000, "line": "x"}]}}

    monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar)

    def fazer():
        engine = _Engine()
        monkeypatch.setattr(rt, "LyricsEngine", lambda *a, **k: engine)
        return engine

    async def rodar(**kw):
        settings = QobuzDLSettings(
            lrc_files=True, embed_lyrics=True, fetch_translation=False
        )
        await rt.process_retroactive_lyrics_async(
            str(raiz), SimpleNamespace(), settings=settings, **kw
        )

    return SimpleNamespace(
        fazer=fazer,
        rodar=rodar,
        consultas=consultas,
        cfg=str(tmp_path / "cfg"),
        raiz=raiz,
    )


async def test_simulacao_pede_dry_run_ao_motor_e_nao_registra_nada(ambiente):
    engine = ambiente.fazer()
    diario = MagicMock()
    progresso = LyricsProgress.open(ambiente.cfg, str(ambiente.raiz))
    await ambiente.rodar(dry_run=True, journal=diario, progress=progresso)
    assert engine.chamadas[0]["dry_run"] is True
    diario.snapshot.assert_not_called()
    diario.record.assert_not_called()
    assert (
        LyricsProgress.open(ambiente.cfg, str(ambiente.raiz)).status_of(
            str(ambiente.raiz / "f1.flac")
        )
        is None
    )


async def test_execucao_normal_nao_manda_dry_run_e_registra_no_diario(ambiente):
    engine = ambiente.fazer()
    diario = MagicMock()
    await ambiente.rodar(journal=diario)
    assert "dry_run" not in engine.chamadas[0]
    diario.snapshot.assert_called_once()
    diario.record.assert_called_once()


async def test_progresso_grava_e_o_incremental_pula(ambiente):
    ambiente.fazer()
    progresso = LyricsProgress.open(ambiente.cfg, str(ambiente.raiz))
    await ambiente.rodar(progress=progresso)
    arquivo = str(ambiente.raiz / "f1.flac")

    de_novo = LyricsProgress.open(ambiente.cfg, str(ambiente.raiz))
    assert de_novo.status_of(arquivo) == "ATUALIZADO" and not de_novo.interrupted

    ambiente.consultas.clear()
    engine = ambiente.fazer()
    de_novo.enable_skip()
    await ambiente.rodar(progress=de_novo)
    assert engine.chamadas == [] and ambiente.consultas == []


async def test_incremental_volta_a_processar_arquivo_alterado(ambiente):
    ambiente.fazer()
    progresso = LyricsProgress.open(ambiente.cfg, str(ambiente.raiz))
    await ambiente.rodar(progress=progresso)
    (ambiente.raiz / "f1.flac").write_bytes(b"mudou")

    engine = ambiente.fazer()
    de_novo = LyricsProgress.open(ambiente.cfg, str(ambiente.raiz))
    de_novo.enable_skip()
    await ambiente.rodar(progress=de_novo)
    assert len(engine.chamadas) == 1


# ----------------------------------------------------------- linha de comando
@pytest.fixture
def parser():
    return qobuz_dl_args(
        default_quality=6, default_limit=20, default_folder="MinhaMusica"
    )


def test_flags_do_lyrics(parser):
    padrao = parser.parse_args(["lyrics"])
    assert not any(
        (
            padrao.dry_run,
            padrao.resume,
            padrao.incremental,
            padrao.no_undo,
            padrao.undo_list,
            padrao.undo_force,
        )
    )
    assert padrao.undo is None
    simulado = parser.parse_args(["lyrics", "--dry-run", "/musicas"])
    assert simulado.dry_run is True and simulado.DIR == "/musicas"
    assert parser.parse_args(["lyrics", "--resume"]).resume is True
    assert parser.parse_args(["lyrics", "--incremental"]).incremental is True
    assert parser.parse_args(["lyrics", "--undo"]).undo == "last"
    assert parser.parse_args(["lyrics", "--undo", "20261013_190000"]).undo == (
        "20261013_190000"
    )
    assert parser.parse_args(["lyrics", "--undo-list"]).undo_list is True
    forcado = parser.parse_args(["lyrics", "--undo", "--undo-force", "--dry-run"])
    assert forcado.undo == "last" and forcado.undo_force and forcado.dry_run


@pytest.mark.parametrize(
    "combinacao",
    [["--resume", "--incremental"], ["--undo", "--undo-list"]],
)
def test_flags_excludentes_do_lyrics(parser, combinacao):
    with pytest.raises(SystemExit):
        parser.parse_args(["lyrics", *combinacao])
