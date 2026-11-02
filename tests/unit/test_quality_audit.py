"""Auditoria de qualidade: pasta e comentário contra o áudio real."""

import pytest

from qobuz_dl import quality_audit as qa

pytestmark = pytest.mark.unit

FLAC16 = {"codec": "FLAC", "bits": 16, "khz": 44.1, "kbps": None}
FLAC24 = {"codec": "FLAC", "bits": 24, "khz": 96.0, "kbps": None}
MP3 = {"codec": "MP3", "bits": None, "khz": 44.1, "kbps": 320}


class TestClaims:
    def test_pasta(self):
        assert qa.folder_claim("Artista - Álbum (2013) [FLAC 24]") == {
            "codec": "FLAC",
            "bits": 24,
        }
        assert qa.folder_claim("Álbum [MP3 16]") == {"codec": "MP3", "bits": None}
        assert qa.folder_claim("Álbum [MP3]") == {"codec": "MP3", "bits": None}
        assert qa.folder_claim("Álbum sem tag") is None

    def test_comentario_flac_e_mp3_novos(self):
        texto = "Fonte: Qobuz\nQualidade: FLAC 24-bit / 96 kHz / Estéreo"
        assert qa.comment_claim(texto)["bits"] == 24
        assert qa.comment_claim("Qualidade: MP3 320 kbps / Estéreo")["kbps"] == 320

    def test_comentario_antigo_com_barras(self):
        claim = qa.comment_claim("Qobuz | 24b/96kHz | Estéreo | HiRes: sim")
        assert (claim["codec"], claim["bits"], claim["khz"]) == (None, 24, 96.0)

    def test_comentario_vazio_ou_sem_qualidade(self):
        assert qa.comment_claim("") is None
        assert qa.comment_claim(None) is None
        assert qa.comment_claim("Ripado por mim") is None


class TestMismatch:
    def test_comentario_flac_diferente_do_audio(self):
        claim = qa.comment_claim("Qualidade: FLAC 24-bit / 96 kHz / Estéreo")
        assert qa.comment_mismatch(claim, FLAC16) is True
        assert qa.comment_mismatch(claim, FLAC24) is False

    def test_comentario_antigo_prometendo_hires_num_mp3(self):
        claim = qa.comment_claim("Qobuz | 24b/96kHz | Estéreo")
        assert qa.comment_mismatch(claim, MP3) is True

    def test_comentario_mp3_usa_tolerancia_de_bitrate(self):
        claim = qa.comment_claim("Qualidade: MP3 320 kbps")
        assert qa.comment_mismatch(claim, MP3) is False
        assert qa.comment_mismatch(claim, dict(MP3, kbps=310)) is False
        assert qa.comment_mismatch(claim, dict(MP3, kbps=128)) is True

    def test_comentario_diz_flac_mas_e_mp3(self):
        claim = qa.comment_claim("Qualidade: FLAC 16-bit / 44.1 kHz")
        assert qa.comment_mismatch(claim, MP3) is True

    def test_sem_comentario_nao_ha_divergencia(self):
        assert qa.comment_mismatch(None, FLAC16) is False

    def test_pasta(self):
        assert qa.folder_mismatch(qa.folder_claim("A [FLAC 24]"), FLAC16) is True
        assert qa.folder_mismatch(qa.folder_claim("A [FLAC 16]"), FLAC16) is False
        assert qa.folder_mismatch(qa.folder_claim("A [FLAC 24]"), MP3) is True
        # "[MP3 16]" é só o padrão antigo: os 16 bits não significam nada no MP3
        assert qa.folder_mismatch(qa.folder_claim("A [MP3 16]"), MP3) is False
        assert qa.folder_mismatch(None, FLAC16) is False


def _detalhes(codec="FLAC", bits=16, taxa=44100, bitrate=900000, comentario=""):
    return {
        "tecnico": {"Codec": codec},
        "_sample_rate": taxa,
        "_bits": bits,
        "_bitrate": bitrate,
        "tags": {"COMMENT": comentario} if comentario else {},
    }


class TestAuditFile:
    def test_descricoes_do_audio_real(self):
        assert qa.describe_real(FLAC16) == "FLAC 16-bit / 44.1 kHz"
        assert qa.describe_real(MP3) == "MP3 320 kbps"

    def test_arquivo_com_pasta_e_comentario_errados(self):
        item = qa.audit_file(
            "RAM [FLAC 24]/05.flac",
            _detalhes(comentario="Qualidade: FLAC 24-bit / 96 kHz / Estéreo"),
        )
        assert item["pasta_errada"] is True
        assert item["pasta_diz"] == "[FLAC 24]"
        assert item["comentario_errado"] is True
        assert item["comentario"] == "24-bit / 96 kHz"
        assert item["real"] == "FLAC 16-bit / 44.1 kHz"

    def test_arquivo_certo(self):
        item = qa.audit_file(
            "RAM [FLAC 16]/05.flac",
            _detalhes(comentario="Qualidade: FLAC 16-bit / 44.1 kHz / Estéreo"),
        )
        assert item["pasta_errada"] is False and item["comentario_errado"] is False

    def test_comentario_e_lido_de_comm_do_id3(self):
        assert qa.comment_from_tags({"COMM::eng": "Qualidade: MP3 320 kbps"}) == (
            "Qualidade: MP3 320 kbps"
        )
        assert qa.comment_from_tags({"TIT2": "x"}) == ""


class TestSummarize:
    def test_conta_cada_pasta_errada_uma_vez(self):
        itens = [
            qa.audit_file(f"RAM [FLAC 24]/0{n}.flac", _detalhes()) for n in range(1, 4)
        ]
        itens.append(
            qa.audit_file(
                "Outro [FLAC 16]/01.flac",
                _detalhes(comentario="Qualidade: FLAC 24-bit / 96 kHz"),
            )
        )
        resumo = qa.summarize(itens)
        assert resumo["conferidas"] == 4
        assert [p["pasta"] for p in resumo["pastas_erradas"]] == ["RAM [FLAC 24]"]
        assert [c["arquivo"] for c in resumo["comentarios_errados"]] == [
            "Outro [FLAC 16]/01.flac"
        ]
