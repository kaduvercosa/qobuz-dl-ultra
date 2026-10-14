"""Testa o registro de DESFAZER do comando `tags` (`qobuz_dl.tags_journal`).

O registro trabalha nos bytes crus do contêiner (blocos de metadados do FLAC e
cabeçalho ID3v2 do MP3), sem mutagen. Por isso os testes montam arquivos
sintéticos mínimos e conferem que o desfazer devolve o arquivo IDÊNTICO,
byte a byte, inclusive o áudio.
"""

import json
import os

import pytest

from qobuz_dl import tags_journal as tj

pytestmark = pytest.mark.unit

AUDIO = b"\xff\xfb\x90\x00" + bytes(range(256)) * 8  # "áudio" de mentira


# --------------------------------------------------------------------------
# Construtores de arquivos sintéticos
# --------------------------------------------------------------------------
def _flac_bytes(comment=b"ARTIST=Antes", picture=b"CAPA-ANTIGA", audio=AUDIO):
    streaminfo = bytes(34)
    blocks = [(0, streaminfo), (4, comment), (6, picture), (1, bytes(64))]
    return tj._join_flac_blocks(blocks) + audio


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _mp3_bytes(frames=b"TPE1\x00\x00\x00\x06\x00\x00\x03Antes", audio=AUDIO):
    header = b"ID3\x04\x00\x00" + _syncsafe(len(frames))
    return header + frames + audio


def _write(path, data):
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def base(tmp_path):
    pasta = tmp_path / "cfg"
    pasta.mkdir()
    return str(pasta)


@pytest.fixture
def journal(base, tmp_path):
    return tj.Journal.create(base, mode="both", root=str(tmp_path))


# --------------------------------------------------------------------------
# Leitura do bloco de metadados
# --------------------------------------------------------------------------
class TestReadHead:
    def test_flac_le_ate_o_primeiro_quadro(self, tmp_path):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        head = tj.read_head(caminho)
        assert head.kind == "flac"
        assert head.data == _flac_bytes()[: -len(AUDIO)]

    def test_mp3_le_o_cabecalho_id3(self, tmp_path):
        caminho = _write(tmp_path / "a.mp3", _mp3_bytes())
        head = tj.read_head(caminho)
        assert head.kind == "mp3"
        assert head.data == _mp3_bytes()[: -len(AUDIO)]

    def test_mp3_com_rodape_v24_inclui_os_10_bytes_do_rodape(self, tmp_path):
        frames = b"TPE1\x00\x00\x00\x06\x00\x00\x03Antes"
        cabecalho = b"ID3\x04\x00\x10" + _syncsafe(len(frames))
        dados = cabecalho + frames + b"3DI" + bytes(7) + AUDIO
        head = tj.read_head(_write(tmp_path / "a.mp3", dados))
        assert len(head.data) == 10 + len(frames) + 10

    @pytest.mark.parametrize(
        "conteudo",
        [b"", b"RIFF....WAVE", b"fLaC", b"fLaC\x00\x00\x00\x22" + bytes(5), b"ID3"],
    )
    def test_formato_desconhecido_ou_truncado_devolve_none(self, tmp_path, conteudo):
        assert tj.read_head(_write(tmp_path / "x.bin", conteudo)) is None

    def test_arquivo_inexistente_devolve_none(self, tmp_path):
        assert tj.read_head(str(tmp_path / "nao-existe.flac")) is None


# --------------------------------------------------------------------------
# Gravar + desfazer (ida e volta exata)
# --------------------------------------------------------------------------
class TestIdaEVolta:
    def test_flac_volta_identico_inclusive_quando_o_bloco_cresce(
        self, tmp_path, base, journal
    ):
        original = _flac_bytes()
        caminho = _write(tmp_path / "a.flac", original)

        assert journal.begin(caminho) is True
        # O "tags" regrava: comentário maior, outra capa, outro tamanho.
        _write(
            tmp_path / "a.flac",
            _flac_bytes(b"ARTIST=Depois" * 20, b"CAPA-NOVA" * 100),
        )
        assert journal.finish(caminho) is True
        assert (tmp_path / "a.flac").read_bytes() != original

        resultado = tj.undo_run(base)
        assert resultado["restored"] == 1
        assert (tmp_path / "a.flac").read_bytes() == original

    def test_mp3_volta_identico(self, tmp_path, base, journal):
        original = _mp3_bytes()
        caminho = _write(tmp_path / "a.mp3", original)
        journal.begin(caminho)
        _write(
            tmp_path / "a.mp3",
            _mp3_bytes(b"TPE1\x00\x00\x00\x0a\x00\x00\x03Depois!!!"),
        )
        assert journal.finish(caminho) is True

        assert tj.undo_run(base)["restored"] == 1
        assert (tmp_path / "a.mp3").read_bytes() == original

    def test_data_de_modificacao_e_restaurada(self, tmp_path, base, journal):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        os.utime(caminho, ns=(1_600_000_000 * 10**9, 1_600_000_000 * 10**9))
        journal.begin(caminho)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Depois"))
        journal.finish(caminho)

        tj.undo_run(base)
        assert os.stat(caminho).st_mtime_ns == 1_600_000_000 * 10**9

    def test_arquivo_sem_mudanca_nao_entra_no_registro(self, tmp_path, base, journal):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        journal.begin(caminho)
        assert journal.finish(caminho) is False
        assert journal.recorded == 0
        assert not os.path.exists(journal.journal_path)
        assert not os.path.exists(journal.pending_path)

    def test_formato_desconhecido_conta_como_sem_backup(self, tmp_path, journal):
        caminho = _write(tmp_path / "a.wav", b"RIFF....WAVE")
        assert journal.begin(caminho) is False
        assert journal.unbacked == 1
        assert journal.finish(caminho) is False

    def test_audio_nunca_e_copiado_para_o_registro(self, tmp_path, base, journal):
        caminho = _write(
            tmp_path / "a.flac", _flac_bytes(audio=b"AUDIO-SECRETO" * 5000)
        )
        journal.begin(caminho)
        _write(
            tmp_path / "a.flac",
            _flac_bytes(b"ARTIST=Depois", audio=b"AUDIO-SECRETO" * 5000),
        )
        journal.finish(caminho)
        tamanho = sum(
            os.path.getsize(os.path.join(raiz, nome))
            for raiz, _, nomes in os.walk(journal.run_dir)
            for nome in nomes
        )
        assert tamanho < 5000  # o áudio de 65 KB não está lá

    def test_mesma_capa_em_varias_faixas_e_guardada_uma_vez(
        self, tmp_path, base, journal
    ):
        capa = b"CAPA-DO-ALBUM" * 200
        for nome in ("a.flac", "b.flac", "c.flac"):
            caminho = _write(tmp_path / nome, _flac_bytes(picture=capa))
            journal.begin(caminho)
            _write(tmp_path / nome, _flac_bytes(b"ARTIST=Depois", picture=b"NOVA"))
            journal.finish(caminho)
        blobs = os.listdir(journal.blobs_dir)
        assert len(blobs) == 1
        assert journal.recorded == 3


# --------------------------------------------------------------------------
# Segurança do desfazer
# --------------------------------------------------------------------------
class TestSegurancaDoDesfazer:
    def _gravar(self, tmp_path, journal):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        journal.begin(caminho)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Depois"))
        journal.finish(caminho)
        return caminho

    def test_arquivo_alterado_depois_e_pulado(self, tmp_path, base, journal):
        caminho = self._gravar(tmp_path, journal)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Outro programa"))
        visto = []
        resultado = tj.undo_run(base, on_file=lambda s, p: visto.append(s))
        assert resultado["changed_since"] == 1 and resultado["restored"] == 0
        assert visto == ["changed_since"]
        assert b"Outro programa" in (tmp_path / "a.flac").read_bytes()

    def test_force_restaura_mesmo_alterado_depois(self, tmp_path, base, journal):
        caminho = self._gravar(tmp_path, journal)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Outro programa"))
        resultado = tj.undo_run(base, force=True)
        assert resultado["restored"] == 1
        assert (tmp_path / "a.flac").read_bytes() == _flac_bytes()

    def test_dry_run_do_desfazer_nao_mexe_em_nada(self, tmp_path, base, journal):
        caminho = self._gravar(tmp_path, journal)
        depois = (tmp_path / "a.flac").read_bytes()
        resultado = tj.undo_run(base, dry_run=True)
        assert resultado["would_restore"] == 1 and resultado["restored"] == 0
        assert (tmp_path / "a.flac").read_bytes() == depois

    def test_arquivo_apagado_e_reportado(self, tmp_path, base, journal):
        caminho = self._gravar(tmp_path, journal)
        os.remove(caminho)
        assert tj.undo_run(base)["missing"] == 1

    def test_desfazer_duas_vezes_nao_estraga(self, tmp_path, base, journal):
        self._gravar(tmp_path, journal)
        tj.undo_run(base)
        segundo = tj.undo_run(base)
        assert segundo["restored"] == 0
        assert (tmp_path / "a.flac").read_bytes() == _flac_bytes()

    def test_nao_deixa_temporarios_para_tras(self, tmp_path, base, journal):
        self._gravar(tmp_path, journal)
        tj.undo_run(base)
        assert [n for n in os.listdir(tmp_path) if n.startswith(".~")] == []


# --------------------------------------------------------------------------
# Interrupção no meio da gravação
# --------------------------------------------------------------------------
class TestPendente:
    def test_recupera_arquivo_gravado_antes_do_finish(self, tmp_path, base, journal):
        original = _flac_bytes()
        caminho = _write(tmp_path / "a.flac", original)
        journal.begin(caminho)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Depois"))
        # Ctrl-C aqui: finish() nunca roda. O pending.json continua no disco.
        assert os.path.exists(journal.pending_path)

        resultado = tj.undo_run(base)
        assert resultado["restored"] == 1
        assert (tmp_path / "a.flac").read_bytes() == original
        assert not os.path.exists(journal.pending_path)

    def test_pending_de_arquivo_que_nao_mudou_e_descartado(
        self, tmp_path, base, journal
    ):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        journal.begin(caminho)
        resultado = tj.undo_run(base)
        assert resultado["restored"] == 0
        assert not os.path.exists(journal.pending_path)


# --------------------------------------------------------------------------
# Execuções guardadas
# --------------------------------------------------------------------------
class TestExecucoes:
    def test_sem_execucoes_devolve_none(self, base):
        assert tj.list_runs(base) == []
        assert tj.undo_run(base) is None

    def test_id_inexistente_devolve_none(self, base, journal):
        assert tj.undo_run(base, run_id="19990101_000000") is None

    def test_list_runs_traz_modo_pasta_e_contagem(self, tmp_path, base, journal):
        caminho = _write(tmp_path / "a.flac", _flac_bytes())
        journal.begin(caminho)
        _write(tmp_path / "a.flac", _flac_bytes(b"ARTIST=Depois"))
        journal.finish(caminho)
        (rodada,) = tj.list_runs(base)
        assert rodada["id"] == journal.run_id
        assert rodada["mode"] == "both"
        assert rodada["root"] == str(tmp_path)
        assert rodada["files"] == 1

    def test_duas_execucoes_no_mesmo_segundo_nao_colidem(self, base, tmp_path):
        a = tj.Journal.create(base, "tags", str(tmp_path))
        b = tj.Journal.create(base, "tags", str(tmp_path))
        assert a.run_dir != b.run_dir

    def test_prune_mantem_so_as_mais_recentes(self, base, tmp_path):
        pasta = os.path.join(base, tj.JOURNAL_DIRNAME)
        os.makedirs(pasta)
        for nome in ("20260101_000000", "20260102_000000", "20260103_000000"):
            os.makedirs(os.path.join(pasta, nome))
        removidas = tj.prune(base, keep=2)
        assert removidas == ["20260101_000000"]
        assert sorted(os.listdir(pasta)) == ["20260102_000000", "20260103_000000"]

    def test_create_apaga_as_execucoes_antigas(self, base, tmp_path):
        pasta = os.path.join(base, tj.JOURNAL_DIRNAME)
        os.makedirs(pasta)
        for i in range(1, 8):
            os.makedirs(os.path.join(pasta, f"2026010{i}_000000"))
        tj.Journal.create(base, "both", str(tmp_path), keep=3)
        assert len(os.listdir(pasta)) == 3

    def test_undo_escolhe_a_mais_recente_por_padrao(self, tmp_path, base):
        antigo = tj.Journal.create(base, "tags", str(tmp_path))
        novo = tj.Journal.create(base, "both", str(tmp_path))
        assert tj.undo_run(base)["run"] == novo.run_id
        assert tj.undo_run(base, run_id=antigo.run_id)["run"] == antigo.run_id

    def test_meta_json_e_valido(self, journal, tmp_path):
        with open(os.path.join(journal.run_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["mode"] == "both" and meta["root"] == str(tmp_path)
