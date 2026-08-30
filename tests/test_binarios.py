"""Testa a checagem dos executaveis externos (ffmpeg, fpcalc).

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
O projeto nao tinha nenhum `shutil.which`. Descobria a ausencia do ffmpeg via
`FileNotFoundError` ao rodar o subprocess dentro de `verify_audio_integrity()`
-- uma funcao que roda **por arquivo**. Um album de 14 faixas gerava 14
mensagens de falha que pareciam 14 arquivos corrompidos, quando o problema era
um so' e era de instalacao, nao de download.

Agora `checar_binarios_externos()` avisa UMA vez na inicializacao, com a
instrucao de instalacao, e `verify_audio_integrity()` consulta o cache.
"""

import logging

import pytest

from qobuz_dl import utils


class TestEncontrarBinario:
    def test_acha_binario_que_existe(self):
        """`sh` existe em qualquer Unix. Se nem isso for achado, a funcao esta
        quebrada e nao e' o ambiente que esta estranho."""
        assert utils.encontrar_binario("sh")

    def test_devolve_none_para_inexistente(self):
        assert utils.encontrar_binario("binario_que_nao_existe_zzz999") is None

    def test_memoriza_o_resultado(self, monkeypatch):
        """O cache existe porque `verify_audio_integrity()` roda por arquivo --
        sem ele, um album de 100 faixas faz 100 varreduras do PATH."""
        utils._BINARIOS_CHECADOS.clear()
        chamadas = []

        def which_espiao(nome, *a, **k):
            chamadas.append(nome)
            return "/caminho/falso/" + nome

        monkeypatch.setattr(utils.shutil, "which", which_espiao)

        for _ in range(5):
            utils.encontrar_binario("ffmpeg")

        assert len(chamadas) == 1, f"PATH varrido {len(chamadas)}x em vez de 1x"
        utils._BINARIOS_CHECADOS.clear()

    def test_memoriza_tambem_a_ausencia(self, monkeypatch):
        """Cachear so' o sucesso deixaria o caso RUIM (binario ausente) fazendo
        uma varredura de PATH por arquivo -- exatamente o cenario lento."""
        utils._BINARIOS_CHECADOS.clear()
        chamadas = []
        monkeypatch.setattr(
            utils.shutil, "which", lambda n, *a, **k: chamadas.append(n) or None
        )
        monkeypatch.setattr(utils, "_DIRS_EXTRA", [])

        for _ in range(5):
            assert utils.encontrar_binario("ffmpeg") is None

        assert len(chamadas) == 1
        utils._BINARIOS_CHECADOS.clear()

    def test_procura_no_appdir_do_a_shell(self, monkeypatch, tmp_path):
        """O a-Shell (iPad) traz ffmpeg nativo em $APPDIR/bin, que nem sempre
        esta no PATH do processo Python. Sem esta busca extra, o usuario de
        iPad receberia "ffmpeg nao encontrado" tendo ffmpeg instalado."""
        utils._BINARIOS_CHECADOS.clear()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        falso = bin_dir / "ffmpeg"
        falso.write_text("#!/bin/sh\n")
        falso.chmod(0o755)

        monkeypatch.setattr(
            utils.shutil,
            "which",
            lambda n, path=None, **k: (str(falso) if path == str(bin_dir) else None),
        )
        monkeypatch.setattr(utils, "_DIRS_EXTRA", [str(bin_dir)])

        assert utils.encontrar_binario("ffmpeg") == str(falso)
        utils._BINARIOS_CHECADOS.clear()


class TestChecarBinariosExternos:
    """Os avisos saem pela UI (`ui.warn` + `ui.wrapped`), nao pelo logging.

    A troca foi deliberada: o `logger.warning` original era uma linha unica de
    ~200 caracteres, e a ponte de logging do projeto serializa a escrita mas
    NAO quebra linha -- num terminal de 32/40 colunas o texto saia cortado no
    meio da palavra. Por isso estes testes leem `capsys`, e nao `caplog`.
    """

    def test_avisa_uma_vez_sobre_ffmpeg_ausente(self, sem_binarios, capsys):
        r = utils.checar_binarios_externos()

        saida = capsys.readouterr().out
        assert r["ffmpeg"] is None
        assert saida.count("ffmpeg nao encontrado") == 1
        # O aviso precisa ser ACIONAVEL: dizer o que instalar.
        assert "instale com" in saida.lower()

    def test_nao_cobra_fpcalc_de_quem_nao_pediu(self, sem_binarios, capsys):
        """Quem so' quer baixar um album nao deve ver aviso de Chromaprint."""
        r = utils.checar_binarios_externos(precisa_fpcalc=False)

        assert r["fpcalc"] is None
        assert "fpcalc" not in capsys.readouterr().out

    def test_cobra_fpcalc_quando_pedido(self, sem_binarios, capsys):
        utils.checar_binarios_externos(precisa_fpcalc=True)

        saida = capsys.readouterr().out
        assert "fpcalc nao encontrado" in saida
        assert "libchromaprint-tools" in saida

    def test_silencio_quando_tudo_esta_presente(self, monkeypatch, capsys):
        utils._BINARIOS_CHECADOS.clear()
        monkeypatch.setattr(utils.shutil, "which", lambda n, *a, **k: "/usr/bin/" + n)

        r = utils.checar_binarios_externos(precisa_fpcalc=True)

        assert r["ffmpeg"] and r["fpcalc"]
        assert capsys.readouterr().out.strip() == ""
        utils._BINARIOS_CHECADOS.clear()

    def test_cai_para_logging_sem_a_ui(self, sem_binarios, monkeypatch, caplog):
        """A funcao tem de continuar usavel fora da CLI (importada por um
        script), onde a UI pode nao estar disponivel."""
        import builtins

        real = builtins.__import__

        def bloqueia_ui(nome, *a, **k):
            if nome == "qobuz_dl" and a and a[2] and "ui" in a[2]:
                raise ImportError("ui indisponivel (simulado)")
            return real(nome, *a, **k)

        monkeypatch.setattr(builtins, "__import__", bloqueia_ui)

        with caplog.at_level(logging.WARNING, logger="qobuz_dl.utils"):
            utils.checar_binarios_externos()

        assert [x for x in caplog.records if "ffmpeg" in x.getMessage()]


class TestVerifyAudioIntegrity:
    def test_sem_ffmpeg_devolve_motivo_claro(self, sem_binarios, tmp_path):
        """ANTES: sem ffmpeg, cada arquivo levantava FileNotFoundError, o que
        na saida parecia corrupcao de audio. DEPOIS: mensagem que diz que a
        VERIFICACAO nao aconteceu -- nao que o arquivo esta ruim."""
        f = tmp_path / "faixa.flac"
        f.write_bytes(b"fLaC" + b"\x00" * 64)

        ok, motivo = utils.verify_audio_integrity(str(f))

        assert ok is False
        assert "ffmpeg" in motivo.lower()
        assert "nao verificada" in motivo.lower()

    def test_arquivo_inexistente(self, tmp_path):
        ok, motivo = utils.verify_audio_integrity(str(tmp_path / "nada.flac"))
        assert ok is False
        assert "nao encontrado" in motivo.lower()


class TestOrdemDaChecagem:
    """Trava o bug de POSICAO da chamada, nao o comportamento dela.

    A chamada de `checar_binarios_externos()` estava depois do bloco que trata
    `--find-duplicates` no cli.py. Como aquele bloco termina em `sys.exit()`,
    o aviso de fpcalc ausente nunca era alcancado por quem usava exatamente a
    feature que depende do fpcalc. O aviso existia e estava correto -- so' era
    inalcancavel.

    Um teste de unidade da funcao passava normalmente com esse bug. So' um
    teste que roda o programa de verdade pega.
    """

    def _rodar_sem_binarios(self, args, tmp_path, ambiente_isolado):
        import subprocess
        import sys

        # PATH com apenas o interpretador: o programa roda, mas nao acha
        # ffmpeg nem fpcalc.
        bin_isolado = tmp_path / "bin"
        bin_isolado.mkdir()
        (bin_isolado / "python3").symlink_to(sys.executable)

        env = dict(ambiente_isolado, PATH=str(bin_isolado))

        r = subprocess.run(
            [sys.executable, "-m", "qobuz_dl", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        return r.stdout + r.stderr

    @pytest.mark.slow
    def test_aviso_de_fpcalc_alcanca_find_duplicates(self, tmp_path, ambiente_isolado):
        """A regressao exata: o aviso tem de aparecer ANTES do sys.exit()."""
        saida = self._rodar_sem_binarios(
            ["--find-duplicates", str(tmp_path)], tmp_path, ambiente_isolado
        )
        assert "fpcalc nao encontrado" in saida

    @pytest.mark.slow
    def test_aviso_de_ffmpeg_aparece_uma_vez_so(self, tmp_path, ambiente_isolado):
        """O ponto todo da checagem centralizada: uma mensagem, nao uma por
        arquivo."""
        saida = self._rodar_sem_binarios(
            ["--find-duplicates", str(tmp_path)], tmp_path, ambiente_isolado
        )
        assert saida.count("[!] ffmpeg nao encontrado") == 1

    @pytest.mark.slow
    def test_avisos_nao_estouram_terminal_estreito(self, tmp_path, ambiente_isolado):
        """`ui.warn()` nao quebra linha -- so' o `ui.wrapped()` do detalhe. Se
        alguem alongar o TITULO de um aviso, ele estoura em terminal estreito.
        Este teste falha nesse caso."""
        import subprocess
        import sys

        bin_isolado = tmp_path / "bin2"
        bin_isolado.mkdir()
        (bin_isolado / "python3").symlink_to(sys.executable)
        env = dict(ambiente_isolado, PATH=str(bin_isolado), COLUMNS="32")

        r = subprocess.run(
            [sys.executable, "-m", "qobuz_dl", "--find-duplicates", str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )

        bloco = [
            linha
            for linha in (r.stdout + r.stderr).splitlines()
            if "nao encontrado" in linha or linha.startswith("    ")
        ]
        estouros = [linha for linha in bloco if len(linha) > 32]
        assert not estouros, "aviso estourou 32 colunas:\n" + "\n".join(estouros)
