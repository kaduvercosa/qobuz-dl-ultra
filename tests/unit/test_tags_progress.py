"""Progresso do `tags`: retomar execução interrompida e rodar só o que mudou."""

import json
import os

import pytest

from qobuz_dl import tags_progress
from qobuz_dl.tags_progress import Progress

pytestmark = pytest.mark.unit


@pytest.fixture
def base(tmp_path):
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    config = tmp_path / "config"
    return pasta, str(config)


def _arq(pasta, nome, dados=b"abc"):
    p = pasta / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dados)
    return str(p)


class TestProgress:
    def test_sem_estado_anterior_nao_ha_nada_pendente(self, base):
        pasta, config = base
        p = Progress.open(config, str(pasta), "both")
        assert not p.known and not p.interrupted
        assert not p.is_done(_arq(pasta, "a.flac"))

    def test_marcado_e_pulado_so_com_skip_ligado(self, base):
        pasta, config = base
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(arq)
        p.finish()

        novo = Progress.open(config, str(pasta), "both")
        assert novo.known and novo.complete and not novo.interrupted
        assert not novo.is_done(arq)  # skip desligado: tudo é refeito
        novo.enable_skip()
        assert novo.is_done(arq)

    def test_arquivo_alterado_depois_volta_a_ser_processado(self, base):
        pasta, config = base
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(arq)
        p.finish()

        with open(arq, "ab") as f:
            f.write(b"mais")  # outro programa mexeu
        novo = Progress.open(config, str(pasta), "both")
        novo.enable_skip()
        assert not novo.is_done(arq)

    def test_execucao_interrompida_e_detectada(self, base):
        pasta, config = base
        a, b = _arq(pasta, "a.flac"), _arq(pasta, "b.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(a)
        p.save()  # como no `finally` de um CTRL+C: sem finish()

        novo = Progress.open(config, str(pasta), "both")
        assert novo.interrupted
        novo.enable_skip()
        assert novo.is_done(a) and not novo.is_done(b)

    def test_cada_modo_tem_o_seu_estado(self, base):
        pasta, config = base
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "tags")
        p.start(keep_done=False)
        p.mark(arq)
        p.finish()

        capas = Progress.open(config, str(pasta), "covers")
        capas.enable_skip()
        assert not capas.known and not capas.is_done(arq)

    def test_cada_pasta_tem_o_seu_estado(self, base, tmp_path):
        pasta, config = base
        outra = tmp_path / "outra"
        outra.mkdir()
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(arq)
        p.finish()
        assert not Progress.open(config, str(outra), "both").known

    @pytest.mark.parametrize("resultado", ["download_failed", "embed_failed", "error"])
    def test_falha_transitoria_nao_marca_o_arquivo(self, base, resultado):
        pasta, config = base
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(arq, resultado)
        p.finish()
        novo = Progress.open(config, str(pasta), "both")
        novo.enable_skip()
        assert not novo.is_done(arq)

    @pytest.mark.parametrize("resultado", [None, "replaced", "kept", "not_found"])
    def test_resultado_definitivo_marca_o_arquivo(self, base, resultado):
        pasta, config = base
        arq = _arq(pasta, "a.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(arq, resultado)
        p.finish()
        novo = Progress.open(config, str(pasta), "both")
        novo.enable_skip()
        assert novo.is_done(arq)

    def test_execucao_completa_zera_o_que_havia(self, base):
        pasta, config = base
        a, b = _arq(pasta, "a.flac"), _arq(pasta, "b.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(a)
        p.finish()

        de_novo = Progress.open(config, str(pasta), "both")
        de_novo.start(keep_done=False)  # execução completa nova
        de_novo.mark(b)
        de_novo.save()  # interrompida
        retomada = Progress.open(config, str(pasta), "both")
        retomada.enable_skip()
        assert retomada.is_done(b) and not retomada.is_done(a)

    def test_incremental_mantem_o_que_ja_estava_pronto(self, base):
        pasta, config = base
        a, b = _arq(pasta, "a.flac"), _arq(pasta, "b.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(a)
        p.finish()

        inc = Progress.open(config, str(pasta), "both")
        inc.enable_skip()
        inc.start(keep_done=True)
        inc.mark(b)
        inc.finish()
        final = Progress.open(config, str(pasta), "both")
        final.enable_skip()
        assert final.is_done(a) and final.is_done(b)

    def test_salva_a_cada_n_arquivos(self, base, monkeypatch):
        pasta, config = base
        monkeypatch.setattr(tags_progress, "SAVE_EVERY", 2)
        a, b = _arq(pasta, "a.flac"), _arq(pasta, "b.flac")
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(a)
        p.mark(b)  # atinge N: grava sem precisar de finish()
        dados = json.load(open(p.state_path, encoding="utf-8"))
        arquivos = dados["roots"][p.root]["both"]["files"]
        assert sorted(arquivos) == ["a.flac", "b.flac"]

    def test_arquivo_de_estado_corrompido_e_ignorado(self, base):
        pasta, config = base
        os.makedirs(config)
        with open(os.path.join(config, tags_progress.FILE_NAME), "w") as f:
            f.write("{isto não é json")
        p = Progress.open(config, str(pasta), "both")
        assert not p.known
        p.start(keep_done=False)  # e é reescrito sem erro
        assert Progress.open(config, str(pasta), "both").known

    def test_versao_desconhecida_e_ignorada(self, base):
        pasta, config = base
        os.makedirs(config)
        with open(os.path.join(config, tags_progress.FILE_NAME), "w") as f:
            json.dump({"version": 99, "roots": {}}, f)
        assert not Progress.open(config, str(pasta), "both").known

    def test_arquivo_que_sumiu_nao_e_marcado(self, base):
        pasta, config = base
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)
        p.mark(str(pasta / "nao_existe.flac"))
        assert p.files == {}

    def test_falha_ao_gravar_estado_nao_derruba(self, base, monkeypatch):
        pasta, config = base

        def falha(*a, **k):
            raise OSError("disco cheio")

        monkeypatch.setattr(tags_progress, "_atomic_write", falha)
        p = Progress.open(config, str(pasta), "both")
        p.start(keep_done=False)  # só registra o aviso
        p.finish()
