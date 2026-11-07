"""`doctor --fix`: só corrige o que é seguro e nunca perde nada."""

import os
import stat

import pytest

from qobuz_dl import doctor
from qobuz_dl.commands import qobuz_dl_args

posix = pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")


def _ini(tmp_path, modo=0o644):
    caminho = tmp_path / "config.ini"
    caminho.write_text("[qobuz]\nemail = a@b.c\n", encoding="utf-8")
    os.chmod(caminho, modo)
    return str(caminho)


def _modo(caminho):
    return stat.S_IMODE(os.stat(caminho).st_mode)


@posix
def test_corrige_permissao_do_config(tmp_path):
    ini = _ini(tmp_path, 0o644)
    fixes = doctor.apply_fixes(config_file=ini, directory=None)
    assert [(f.name, f.ok) for f in fixes] == [("Permissão do config", True)]
    assert _modo(ini) == 0o600


@posix
def test_config_ja_restrito_nao_gera_correcao(tmp_path):
    ini = _ini(tmp_path, 0o600)
    assert doctor.apply_fixes(config_file=ini, directory=None) == []


@posix
def test_nao_mexe_no_conteudo_do_config(tmp_path):
    ini = _ini(tmp_path, 0o666)
    antes = open(ini, encoding="utf-8").read()
    doctor.apply_fixes(config_file=ini, directory=None)
    assert open(ini, encoding="utf-8").read() == antes


def test_cria_a_pasta_de_downloads_que_falta(tmp_path):
    ini = _ini(tmp_path, 0o600)
    destino = tmp_path / "a" / "b"
    fixes = doctor.apply_fixes(config_file=ini, directory=str(destino))
    assert destino.is_dir()
    assert [(f.name, f.ok) for f in fixes] == [("Pasta de downloads", True)]


def test_pasta_existente_nao_e_tocada(tmp_path):
    ini = _ini(tmp_path, 0o600)
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "a.flac").write_bytes(b"x")
    assert doctor.apply_fixes(config_file=ini, directory=str(tmp_path / "m")) == []
    assert (tmp_path / "m" / "a.flac").exists()


def test_falha_ao_criar_pasta_vira_correcao_com_ok_falso(tmp_path):
    ini = _ini(tmp_path, 0o600)
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("x")
    fixes = doctor.apply_fixes(config_file=ini, directory=str(arquivo / "sub"))
    assert len(fixes) == 1 and fixes[0].ok is False


def test_sem_config_e_sem_pasta_nao_faz_nada(tmp_path):
    assert doctor.apply_fixes(config_file=str(tmp_path / "x.ini"), directory=None) == []


@posix
def test_depois_do_fix_o_doctor_nao_avisa_mais_da_permissao(tmp_path):
    ini = _ini(tmp_path, 0o644)
    antes = [c for c in doctor.check_config(ini) if c.name == "Permissão do config"]
    assert antes[0].level == doctor.WARN
    doctor.apply_fixes(config_file=ini, directory=None)
    depois = [c for c in doctor.check_config(ini) if c.name == "Permissão do config"]
    assert depois[0].level == doctor.PASS


def test_render_fixes_mostra_o_que_foi_feito(capsys):
    doctor.render_fixes(
        [
            doctor.Fix("Permissão do config", True, "0o644 -> 0o600"),
            doctor.Fix("X", False, "erro"),
        ]
    )
    saida = capsys.readouterr().out
    assert "CORREÇÕES" in saida and "0o644 -> 0o600" in saida and "erro" in saida


def test_render_fixes_sem_nada(capsys):
    doctor.render_fixes([])
    assert "Nada a corrigir" in capsys.readouterr().out


def test_flag_fix_no_parser():
    assert qobuz_dl_args().parse_args(["doctor", "--fix"]).fix is True
    assert qobuz_dl_args().parse_args(["doctor"]).fix is False
