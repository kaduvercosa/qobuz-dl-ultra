"""Origem da letra gravada no metadata: COMMENT e frame USLT do MP3."""

import pytest

from qobuz_dl import lyrics_engine as le
from qobuz_dl.lyrics_engine import LyricsEngine

pytestmark = pytest.mark.unit

SYNC = "[00:01.00]um\n[00:05.00]dois"


class _FakeFlac(dict):
    salvo = False

    def save(self, *a, **k):
        type(self).salvo = True


class _ID3(le.ID3):
    """ID3 de verdade (só contêiner de frames), sem gravar em disco."""

    def save(self, *a, **k):
        pass


def _gravar(caminho, **kw):
    # `_inject_metadata` não usa o estado da engine: dá pra chamar sem construí-la.
    return LyricsEngine._inject_metadata(None, str(caminho), SYNC, **kw)


class TestFlac:
    def test_grava_a_origem_no_comment_e_mantem_o_resto(self, monkeypatch, tmp_path):
        audio = _FakeFlac(COMMENT=["Fonte: Qobuz\nFaixa Qobuz: https://x/1"])
        monkeypatch.setattr(le, "FLAC", lambda path: audio)
        assert _gravar(
            tmp_path / "a.flac", source="Qobuz", language="en+pt", bilingual=True
        )
        linhas = audio["COMMENT"].splitlines()
        assert linhas[:2] == ["Fonte: Qobuz", "Faixa Qobuz: https://x/1"]
        assert linhas[-1] == "Letra: Qobuz (sincronizada, bilíngue EN+PT)"
        assert audio["LYRICS_SOURCE"] == "Qobuz"

    def test_troca_a_linha_de_letra_anterior(self, monkeypatch, tmp_path):
        audio = _FakeFlac(COMMENT=["Fonte: Qobuz\nLetra: LRCLIB (sincronizada)"])
        monkeypatch.setattr(le, "FLAC", lambda path: audio)
        assert _gravar(tmp_path / "a.flac", source="BiniLyrics", language="en")
        linhas = audio["COMMENT"].splitlines()
        assert linhas.count("Letra: BiniLyrics (sincronizada, EN)") == 1
        assert not any("LRCLIB" in linha for linha in linhas)

    def test_sem_fonte_nao_mexe_no_comment(self, monkeypatch, tmp_path):
        audio = _FakeFlac(COMMENT=["Fonte: Qobuz"])
        monkeypatch.setattr(le, "FLAC", lambda path: audio)
        assert _gravar(tmp_path / "a.flac", source=None)
        assert audio["COMMENT"] == ["Fonte: Qobuz"]


class TestMp3:
    def _id3(self, monkeypatch, *frames):
        audio = _ID3()
        for frame in frames:
            audio.add(frame)
        monkeypatch.setattr(le, "ID3", lambda path: audio)
        return audio

    def test_letra_nova_substitui_a_de_outra_fonte(self, monkeypatch, tmp_path):
        # O frame USLT é identificado pela descrição (a fonte): sem apagar os
        # antigos, a letra do Musixmatch ficava ao lado da nova.
        audio = self._id3(
            monkeypatch,
            le.USLT(encoding=3, lang="eng", desc="Musixmatch", text="[00:01.00]velha"),
        )
        assert _gravar(tmp_path / "a.mp3", source="Qobuz", language="en")
        frames = audio.getall("USLT")
        assert len(frames) == 1
        assert frames[0].desc == "Qobuz"

    def test_grava_a_origem_no_comm_e_mantem_os_outros_trechos(
        self, monkeypatch, tmp_path
    ):
        audio = self._id3(
            monkeypatch,
            le.COMM(
                encoding=3,
                lang="eng",
                desc="",
                text=["Qobuz | Trk ID: 1 | Letra: LRCLIB (sincronizada)"],
            ),
        )
        assert _gravar(
            tmp_path / "a.mp3", source="Qobuz", language="en+pt", bilingual=True
        )
        (comm,) = audio.getall("COMM")
        assert comm.text == [
            "Qobuz | Trk ID: 1 | Letra: Qobuz (sincronizada, bilíngue EN+PT)"
        ]

    def test_sem_comm_previo_cria_o_frame(self, monkeypatch, tmp_path):
        audio = self._id3(monkeypatch)
        assert _gravar(tmp_path / "a.mp3", source="BiniLyrics", language="en")
        (comm,) = audio.getall("COMM")
        assert comm.text == ["Letra: BiniLyrics (sincronizada, EN)"]
