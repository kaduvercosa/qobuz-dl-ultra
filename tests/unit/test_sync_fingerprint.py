"""Testa qobuz_dl/sync.py::_compute_fingerprint -- o sistema de 3 níveis
de identificação de faixa duplicada (MD5 nativo do FLAC > ID universal >
metadados normalizados).

COMO TESTAR SEM ARQUIVO DE ÁUDIO DE VERDADE
---------------------------------------------
`_compute_fingerprint` importa `FLAC`, `ID3` e `File` do mutagen direto
pro namespace de sync.py (`from mutagen.flac import FLAC` etc.) -- então
dá pra trocar essas TRÊS classes por dublês (`monkeypatch.setattr(sync,
"FLAC", ...)`) que devolvem exatamente os dados que cada cenário precisa,
sem nunca abrir um arquivo .flac/.mp3 real. Mesmo princípio de todo teste
desta sessão que troca uma dependência externa por um fake controlável.

SOBRE A duration=0 NOS CAMINHOS RÁPIDOS DE MP3
-------------------------------------------------
Reparei que os `return duration, ...` dentro do bloco `elif
filepath.lower().endswith(".mp3")` devolvem `duration=0` sempre (o valor
só é preenchido no bloco do FLAC ou no fallback genérico via
`audio.info.length`). Não é um bug que vale corrigir: `duration` é
literalmente descartado pelo único chamador (`find_duplicate_tracks`, que
faz `duration, fp = ...` e só usa `fp` daí em diante) -- então os testes
abaixo documentam esse `0` como comportamento atual, não como algo a
consertar.
"""

from types import SimpleNamespace


from qobuz_dl import sync
from qobuz_dl.sync import _compute_fingerprint


# --------------------------------------------------------------------
# Dublês das classes do mutagen
# --------------------------------------------------------------------
class _FakeFLAC(dict):
    """Objetos FLAC do mutagen se comportam como dict de listas (tags
    Vorbis Comment) + um atributo `.info` com metadados do áudio."""

    def __init__(self, tags=None, length=200, md5_signature=0):
        super().__init__(tags or {})
        self.info = SimpleNamespace(length=length, md5_signature=md5_signature)


class _FakeID3Frame:
    def __init__(self, texto):
        self.text = [texto]


class _FakeID3(dict):
    pass


class _FakeEasyFile:
    """Substitui `mutagen.File(path, easy=True)` -- usado no fallback
    genérico de metadados (funciona pra qualquer formato)."""

    def __init__(self, tags=None, length=50):
        self._tags = tags or {}
        self.info = SimpleNamespace(length=length)

    def get(self, chave, default=None):
        return self._tags.get(chave, default)


# --------------------------------------------------------------------
# Caminho FLAC
# --------------------------------------------------------------------
class TestFingerprintFlac:
    def test_md5_nativo_tem_prioridade_maxima(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "FLAC",
            lambda path: _FakeFLAC(
                tags={"QOBUZTRACKID": ["deveria-ser-ignorado"]},
                length=180,
                md5_signature=123456789,
            ),
        )
        duration, fp = _compute_fingerprint("faixa.flac")
        assert duration == 180
        assert fp == "flac_audio_md5:123456789"

    def test_sem_md5_usa_qobuz_track_id(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "FLAC",
            lambda path: _FakeFLAC(
                tags={"QOBUZTRACKID": ["abc123"]}, length=200, md5_signature=0
            ),
        )
        duration, fp = _compute_fingerprint("faixa.flac")
        assert duration == 200
        assert fp == "qobuz_id:abc123"

    def test_sem_md5_e_sem_qobuz_id_usa_isrc(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "FLAC",
            lambda path: _FakeFLAC(
                tags={"isrc": ["GBAYE0000001"]}, length=210, md5_signature=0
            ),
        )
        duration, fp = _compute_fingerprint("faixa.flac")
        assert duration == 210
        assert fp == "isrc:GBAYE0000001"

    def test_case_do_nome_da_extensao_nao_importa(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "FLAC",
            lambda path: _FakeFLAC(md5_signature=999),
        )
        duration, fp = _compute_fingerprint("Faixa.FLAC")
        assert fp == "flac_audio_md5:999"

    def test_flac_sem_nenhum_id_cai_pro_fallback_de_metadados(self, monkeypatch):
        # md5=0, sem QOBUZTRACKID, sem isrc -- o if do FLAC termina sem
        # nenhum return, e a execução continua pro File(easy=True) genérico
        # (NÃO entra no elif do mp3, porque o if do .flac já era True).
        monkeypatch.setattr(sync, "FLAC", lambda path: _FakeFLAC(md5_signature=0))
        monkeypatch.setattr(
            sync,
            "File",
            lambda path, easy: _FakeEasyFile(
                tags={"title": ["Faixa"], "artist": ["Artista"], "album": ["Álbum"]},
                length=150,
            ),
        )
        duration, fp = _compute_fingerprint("faixa.flac")
        assert duration == 150
        assert fp.startswith("meta_hash:")


# --------------------------------------------------------------------
# Caminho MP3
# --------------------------------------------------------------------
class TestFingerprintMp3:
    def test_txxx_qobuz_track_id_tem_prioridade(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "ID3",
            lambda path: _FakeID3({"TXXX:QOBUZTRACKID": _FakeID3Frame("xyz789")}),
        )
        duration, fp = _compute_fingerprint("faixa.mp3")
        assert fp == "qobuz_id:xyz789"
        assert duration == 0  # ver nota no topo do arquivo

    def test_sem_txxx_usa_tsrc_isrc(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "ID3",
            lambda path: _FakeID3({"TSRC": _FakeID3Frame("USRC12345678")}),
        )
        duration, fp = _compute_fingerprint("faixa.mp3")
        assert fp == "isrc:USRC12345678"

    def test_mp3_sem_nenhum_id_cai_pro_fallback_de_metadados(self, monkeypatch):
        monkeypatch.setattr(sync, "ID3", lambda path: _FakeID3({}))
        monkeypatch.setattr(
            sync,
            "File",
            lambda path, easy: _FakeEasyFile(tags={"title": ["Faixa MP3"]}, length=90),
        )
        duration, fp = _compute_fingerprint("faixa.mp3")
        assert duration == 90
        assert fp.startswith("meta_hash:")


# --------------------------------------------------------------------
# Fallback genérico (extensão que não é .flac nem .mp3, ex.: .m4a)
# --------------------------------------------------------------------
class TestFingerprintFallbackGenerico:
    def test_metadados_normalizados_compoem_o_meta_hash(self, monkeypatch):
        monkeypatch.setattr(
            sync,
            "File",
            lambda path, easy: _FakeEasyFile(
                tags={
                    "artist": ["Café Tacvba"],
                    "album": ["Re"],
                    "title": ["Ojalá que Llueva Café"],
                },
                length=240,
            ),
        )
        duration, fp = _compute_fingerprint("faixa.m4a")
        assert duration == 240
        # Acentos removidos (NFKD -> ASCII) e tudo em minúsculo -- é isso
        # que faz duas faixas com acentuação digitada diferente (ou
        # ausente) baterem como a MESMA fingerprint.
        assert fp == "meta_hash:cafe tacvba|re|ojala que llueva cafe|240"

    def test_tags_ausentes_viram_string_vazia_sem_lancar(self, monkeypatch):
        monkeypatch.setattr(
            sync, "File", lambda path, easy: _FakeEasyFile(tags={}, length=30)
        )
        duration, fp = _compute_fingerprint("faixa.ogg")
        assert duration == 30
        assert fp == "meta_hash:|||30"

    def test_file_none_devolve_none_none(self, monkeypatch):
        # mutagen.File() devolve None pra formato não reconhecido/corrompido.
        monkeypatch.setattr(sync, "File", lambda path, easy: None)
        duration, fp = _compute_fingerprint("arquivo-corrompido.xyz")
        assert (duration, fp) == (None, None)


# --------------------------------------------------------------------
# Falhas inesperadas
# --------------------------------------------------------------------
class TestFingerprintErros:
    def test_excecao_qualquer_e_capturada_devolve_none_none(self, monkeypatch):
        def _explode(path):
            raise OSError("arquivo ilegível")

        monkeypatch.setattr(sync, "FLAC", _explode)
        duration, fp = _compute_fingerprint("faixa-corrompida.flac")
        assert (duration, fp) == (None, None)

    def test_erro_no_fallback_generico_tambem_e_capturado(self, monkeypatch):
        monkeypatch.setattr(
            sync, "File", lambda path, easy: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        duration, fp = _compute_fingerprint("faixa.m4a")
        assert (duration, fp) == (None, None)
