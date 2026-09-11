"""Testes unitários para qobuz_dl/lyrics_engine.py.

POR QUE A FIXTURE `engine` USA UM SESSION FAKE EM VEZ DE `LyricsEngine()`
---------------------------------------------------------------------------
`LyricsEngine.__init__` só cria um `httpx.Client(follow_redirects=True)`
de verdade quando `session` é `None` -- o `__init__` NÃO faz nenhum
`isinstance` check no valor passado, então qualquer objeto serve pra
pular a criação do client real (ver `qobuz_dl/lyrics_engine.py`, linhas
50-55). Criar um `httpx.Client` de verdade abre um contexto SSL (vários
file descriptors). Nenhum dos testes abaixo faz requisição de rede
nenhuma -- testam só formatação de LRC, extração de letra, gravação de
arquivo/tag -- então o cliente HTTP é puro overhead aqui, e nem precisa
existir de verdade.

Uma primeira tentativa de correção usou uma fixture `scope="module"` com
`LyricsEngine()` (client real, mas UM só pro módulo inteiro, fechado no
teardown). Isso não resolveu: quando o setup de uma fixture levanta
exceção, o pytest NÃO cacheia a falha -- ele tenta recriar a fixture pra
cada teste que dependa dela. Então mesmo module-scoped, a criação do
client real era retentada em todos os 8 testes, e cada tentativa disputa
o mesmo orçamento de file descriptors já quase esgotado pela suíte
inteira (812 testes antes deste arquivo) num ambiente com `ulimit -n`
bem apertado (a-Shell/iOS).

A fixture abaixo passa um `session` fake (`MagicMock()`), então
`LyricsEngine.__init__` nunca cria um `httpx.Client` real -- este arquivo
passa a contribuir com ZERO file descriptors de sessão HTTP pro
processo, em vez de 1 (module-scoped) ou 8 (por teste). Isso resolve o
`OSError: Too many open files` deste arquivo independente do que estiver
vazando fds mais cedo na suíte -- mas não é a causa raiz: algo antes
deste módulo ainda está deixando file descriptors abertos, e vale a pena
rastrear separadamente (ex.: `ulimit -n` antes de rodar a suíte, ou
localizar outros `httpx.Client`/arquivos não fechados em outros módulos
de teste).
"""

from unittest.mock import MagicMock

import pytest

import qobuz_dl.lyrics_engine as le_module
from qobuz_dl.lyrics_engine import LyricsEngine


@pytest.fixture(scope="module")
def engine():
    eng = LyricsEngine(session=MagicMock())
    yield eng
    eng.close()


def test_ms_to_lrc_timestamp():
    assert LyricsEngine._ms_to_lrc_timestamp(0) == "[00:00.000]"
    assert LyricsEngine._ms_to_lrc_timestamp(65432) == "[01:05.432]"


def test_qobuz_lines_to_lrc(engine):
    lines = [
        {"start": 1000, "line": "Primeira linha"},
        {"start": 5000, "line": "Segunda linha"},
    ]
    lrc_intro = engine._qobuz_lines_to_lrc(lines, inject_intro=True)
    assert "[00:00.000] » » » " in lrc_intro
    assert "[00:01.000] Primeira linha" in lrc_intro

    lrc_no_intro = engine._qobuz_lines_to_lrc(lines, inject_intro=False)
    assert "[00:00.000]" not in lrc_no_intro
    assert "[00:01.000] Primeira linha" in lrc_no_intro


def test_qobuz_lines_to_plain(engine):
    lines = [
        {"start": 1000, "line": "Linha 1"},
        {"start": 5000, "line": "Linha 2"},
    ]
    plain = engine._qobuz_lines_to_plain(lines)
    assert plain == "Linha 1\nLinha 2"


def test_extract_qobuz_lyrics(engine):
    raw = {
        "original": {
            "lang": "en",
            "lines": [
                {"start": 1000, "line": "Hello world"},
            ],
        }
    }
    extracted = engine.extract_qobuz_lyrics(raw)
    assert extracted["source"] == "qobuz"
    assert extracted["lang"] == "en"
    assert "Hello world" in extracted["plain"]


def test_build_bilingual_lrc(engine):
    orig = "[00:01.000] Hello"
    trans = "[00:01.000] Olá"
    bilingual = engine._build_bilingual_lrc(orig, trans)
    assert "[00:01.000] Hello" in bilingual
    assert "[00:01.000] » Olá" in bilingual


def test_inject_instrumental_pauses(engine):
    lrc = "[00:01.000] Linha 1\n[00:20.000] Linha 2"
    with_pauses = engine._inject_instrumental_pauses(lrc)
    assert "• • •" in with_pauses


def test_save_lrc_file(engine, tmp_path):
    audio_file = tmp_path / "test.flac"
    audio_file.write_bytes(b"dummy")

    saved = engine._save_lrc_file(
        str(audio_file), "[00:01.000] Teste", source="Qobuz", language="pt"
    )
    assert saved is True
    lrc_file = tmp_path / "test.lrc"
    assert lrc_file.exists()
    content = lrc_file.read_text(encoding="utf-8")
    assert "[by:Qobuz]" in content
    assert "[la:pt]" in content
    assert "[00:01.000] Teste" in content


class FakeFLAC(dict):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.saved = False

    def save(self):
        self.saved = True


class FakeID3:
    def __init__(self, *args, **kwargs):
        self.added = []
        self.deleted = []
        self.saved = False

    def add(self, frame):
        self.added.append(frame)

    def delall(self, desc):
        self.deleted.append(desc)

    def save(self, file_path=None):
        self.saved = True


def test_inject_metadata_flac(engine, tmp_path, monkeypatch):
    flac_file = tmp_path / "song.flac"
    flac_file.write_bytes(b"dummy")

    fake_flac = FakeFLAC()
    monkeypatch.setattr(le_module, "FLAC", lambda path: fake_flac)

    ok = engine._inject_metadata(
        str(flac_file),
        "Letra de teste",
        source="Qobuz",
        language="pt",
        bilingual=True,
    )
    assert ok is True
    assert fake_flac["LYRICS"] == "Letra de teste"
    assert fake_flac["LYRICS_SOURCE"] == "Qobuz"
    assert fake_flac["LYRICS_LANG"] == "pt"
    assert fake_flac["LYRICS_BILINGUAL"] == "1"
    assert fake_flac.saved is True


def test_inject_metadata_mp3(engine, tmp_path, monkeypatch):
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(b"dummy")

    fake_id3 = FakeID3()
    monkeypatch.setattr(le_module, "ID3", lambda path=None: fake_id3)

    ok = engine._inject_metadata(
        str(mp3_file),
        "Letra MP3",
        source="LRCLIB",
        language="en",
        bilingual=False,
    )
    assert ok is True
    assert len(fake_id3.added) > 0
    assert fake_id3.saved is True
