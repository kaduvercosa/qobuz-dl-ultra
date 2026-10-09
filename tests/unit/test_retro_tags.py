"""Testa qobuz_dl/retro_tags.py -- comando `tags` (correção retroativa de tags).

Foco: a descoberta de arquivos olha o DISCO (nunca o banco), o modo
multi-tags chega até as funções de tag (ligado e desligado), o tagueamento
é feito no próprio arquivo (sem renomear) e os contadores batem.
"""

import asyncio
import os

import pytest

from qobuz_dl import retro_tags


class _Settings:
    def __init__(self, multi):
        self.multi_value_tags = multi


class _FakeClient:
    def __init__(self):
        self.track_calls = 0
        self.album_calls = 0

    async def get_track_meta(self, track_id):
        self.track_calls += 1
        if str(track_id) == "404":
            raise RuntimeError("não encontrada")
        return {"id": track_id, "album": {"id": "ALB1"}}

    async def get_album_meta(self, album_id):
        self.album_calls += 1
        return {"id": album_id, "tracks": {"items": [{"id": 1}, {"id": 2}]}}


@pytest.fixture
def biblioteca(tmp_path):
    for nome in ("a.flac", "b.flac", "c.flac", "d.mp3", "nota.txt", "~tmp_01.tmp"):
        (tmp_path / nome).write_bytes(b"")
    sub = tmp_path / "CD 02"
    sub.mkdir()
    (sub / "e.flac").write_bytes(b"")
    return tmp_path


class TestFindAudioFiles:
    def test_so_audio_que_existe_no_disco_e_ignora_tmp(self, biblioteca):
        achados = retro_tags.find_audio_files(str(biblioteca))
        nomes = [os.path.relpath(p, biblioteca) for p in achados]
        assert nomes == sorted(
            ["a.flac", "b.flac", "c.flac", "d.mp3", os.path.join("CD 02", "e.flac")]
        )

    def test_pasta_vazia(self, tmp_path):
        assert retro_tags.find_audio_files(str(tmp_path)) == []


@pytest.mark.parametrize("multi", [True, False])
class TestRetagDirectory:
    def _preparar(self, monkeypatch, biblioteca):
        ids = {
            "a.flac": "1",
            "b.flac": "2",
            "c.flac": None,
            "d.mp3": "404",
            "e.flac": "1",
        }
        chamadas = []
        estado = {}

        def fake_flac(
            filename, root, final, item, album, istrack, em_image, settings=None, **kw
        ):
            chamadas.append(
                (
                    filename,
                    final,
                    item["id"],
                    istrack,
                    em_image,
                    settings.multi_value_tags,
                )
            )
            estado[filename] = {
                "ARTIST": ["A", "B"] if settings.multi_value_tags else ["A, B"]
            }

        monkeypatch.setattr(retro_tags.metadata, "tag_flac", fake_flac)
        monkeypatch.setattr(
            retro_tags, "extract_track_id", lambda p: ids.get(os.path.basename(p))
        )
        monkeypatch.setattr(retro_tags, "extract_album_id", lambda p: None)
        monkeypatch.setattr(
            retro_tags,
            "snapshot_tags",
            lambda p: {k: list(v) for k, v in estado.get(p, {}).items()},
        )
        return chamadas

    def test_modo_multi_tags_chega_nas_funcoes_de_tag(
        self, monkeypatch, biblioteca, multi
    ):
        chamadas = self._preparar(monkeypatch, biblioteca)
        client = _FakeClient()
        stats = asyncio.run(
            retro_tags.retag_directory(str(biblioteca), client, _Settings(multi))
        )

        assert stats["total"] == 5
        assert stats["updated"] == 3  # a, b, e
        assert stats["no_id"] == 1  # c
        assert stats["errors"] == 1  # d (404)
        assert all(c[5] is multi for c in chamadas)

    def test_tagueia_no_proprio_arquivo_sem_mexer_na_capa(
        self, monkeypatch, biblioteca, multi
    ):
        chamadas = self._preparar(monkeypatch, biblioteca)
        asyncio.run(
            retro_tags.retag_directory(str(biblioteca), _FakeClient(), _Settings(multi))
        )
        for filename, final, _, istrack, em_image, _ in chamadas:
            assert filename == final  # sem renomear
            assert istrack is False  # item veio do álbum completo
            assert em_image is False

    def test_album_e_buscado_uma_vez_e_segunda_passada_nao_muda_nada(
        self, monkeypatch, biblioteca, multi
    ):
        self._preparar(monkeypatch, biblioteca)
        client = _FakeClient()
        settings = _Settings(multi)
        asyncio.run(retro_tags.retag_directory(str(biblioteca), client, settings))
        assert client.album_calls == 1

        stats = asyncio.run(
            retro_tags.retag_directory(str(biblioteca), client, settings)
        )
        assert stats["updated"] == 0
        assert stats["unchanged"] == 3


def test_pasta_inexistente_no_dispositivo_nao_quebra(tmp_path):
    inexistente = str(tmp_path / "nao_existe")
    assert (
        asyncio.run(
            retro_tags.retag_directory(inexistente, _FakeClient(), _Settings(True))
        )
        is None
    )


def test_multi_tags_label():
    assert retro_tags.multi_tags_label(_Settings(True)) == "ATIVADO"
    assert retro_tags.multi_tags_label(_Settings(False)) == "DESATIVADO"
