"""Testes adicionais de qobuz_dl/doctor.py: cada checagem isolada, com
caminhos temporários, sem rede e sem tocar no ambiente real.
"""

import os
import sqlite3
import sys
from collections import namedtuple
from types import SimpleNamespace

import pytest

from qobuz_dl import doctor
from qobuz_dl.doctor import FAIL, PASS, WARN, Check

pytestmark = pytest.mark.unit

CONFIG_OK = """[qobuz]
email = a@b.c
app_id = 123
secrets = abc
default_quality = 6
directory = {directory}
"""


def _niveis(checks):
    return {c.name: c.level for c in checks}


def test_check_python_aprova_versao_atual():
    assert doctor.check_python().level == PASS


def test_check_python_reprova_versao_antiga(monkeypatch):
    V = namedtuple("V", "major minor micro releaselevel serial")
    falso = SimpleNamespace(version_info=V(3, 9, 1, "final", 0))
    monkeypatch.setattr(doctor, "sys", falso)
    r = doctor.check_python()
    assert r.level == FAIL
    assert "3.9.1" in r.detail


def test_check_modules_todos_presentes(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda _m: object())
    r = doctor.check_modules()
    assert r[0] == Check(PASS, "Dependências", "todas as obrigatórias importam")
    assert all(c.level == PASS for c in r)
    assert len(r) == 1 + len(doctor.OPTIONAL_MODULES)


def test_check_modules_lista_faltantes_e_opcionais(monkeypatch):
    ausentes = {"mutagen", "brotli"}

    def fake(nome):
        if nome in ausentes:
            raise ImportError(nome)
        return object()

    monkeypatch.setattr(doctor.importlib, "import_module", fake)
    r = doctor.check_modules()
    assert r[0].level == FAIL
    assert "mutagen" in r[0].detail
    niveis = _niveis(r)
    assert niveis["Opcional: brotli"] == WARN
    assert niveis["Opcional: PIL"] == PASS


def test_check_binaries(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None
    )
    r = doctor.check_binaries()
    assert r[0] == Check(PASS, "ffmpeg", "/usr/bin/ffmpeg")
    assert [c.level for c in r[1:]] == [WARN, WARN]
    assert "PATH" in r[1].detail


def test_check_config_inexistente(tmp_path):
    r = doctor.check_config(str(tmp_path / "nada.ini"))
    assert len(r) == 1
    assert r[0].level == FAIL


@pytest.fixture
def keyring_falso(monkeypatch):
    def instala(nome_classe="SecretService", erro=None):
        classe = type(nome_classe, (), {})
        classe.__module__ = "keyring.backends.x"

        def get_keyring():
            if erro:
                raise erro
            return classe()

        monkeypatch.setitem(
            sys.modules, "keyring", SimpleNamespace(get_keyring=get_keyring)
        )

    return instala


def _cfg(tmp_path, texto, modo=0o600):
    p = tmp_path / "config.ini"
    p.write_text(texto, encoding="utf-8")
    p.chmod(modo)
    return str(p)


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_check_config_permissao_aberta_gera_warn(tmp_path, keyring_falso):
    keyring_falso()
    r = doctor.check_config(_cfg(tmp_path, CONFIG_OK, 0o644))
    assert _niveis(r)["Permissão do config"] == WARN


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_check_config_valido_com_keyring_seguro(tmp_path, keyring_falso):
    keyring_falso()
    r = doctor.check_config(_cfg(tmp_path, CONFIG_OK.format(directory="/x")))
    niveis = _niveis(r)
    assert niveis["config.ini"] == PASS
    assert niveis["Permissão do config"] == PASS
    assert niveis["Keyring"] == PASS
    assert FAIL not in niveis.values()


def test_check_config_sintaxe_invalida(tmp_path):
    r = doctor.check_config(_cfg(tmp_path, "isto nao e ini\n"))
    assert r[-1].name == "Sintaxe do config"
    assert r[-1].level == FAIL


def test_check_config_campos_vazios_reprovam(tmp_path, keyring_falso):
    keyring_falso()
    r = doctor.check_config(_cfg(tmp_path, "[qobuz]\nemail =\n"))
    falhas = {c.name for c in r if c.level == FAIL}
    assert falhas == {
        "config: email",
        "config: app_id",
        "config: secrets",
        "config: default_quality",
    }


def test_check_config_secao_default(tmp_path, keyring_falso):
    keyring_falso()
    texto = "[DEFAULT]\nemail=a\napp_id=1\nsecrets=s\ndefault_quality=6\n"
    r = doctor.check_config(_cfg(tmp_path, texto))
    assert not [c for c in r if c.level == FAIL]


def test_check_config_token_em_texto_puro(tmp_path):
    texto = (
        CONFIG_OK.format(directory="/x") + "disable_keyring = true\nauth_token = t\n"
    )
    r = doctor.check_config(_cfg(tmp_path, texto))
    assert _niveis(r)["Token"] == WARN
    assert "t" not in {c.detail for c in r if c.name == "Token"}


def test_check_config_keyring_desativado_sem_token_nao_checa_backend(tmp_path):
    texto = CONFIG_OK.format(directory="/x") + "disable_keyring = yes\n"
    names = _niveis(doctor.check_config(_cfg(tmp_path, texto)))
    assert "Token" not in names
    assert "Keyring" not in names


@pytest.mark.parametrize("nome", ["FailKeyring", "NullKeyring"])
def test_check_config_keyring_sem_backend_seguro(tmp_path, keyring_falso, nome):
    keyring_falso(nome)
    r = doctor.check_config(_cfg(tmp_path, CONFIG_OK.format(directory="/x")))
    assert _niveis(r)["Keyring"] == WARN


def test_check_config_keyring_indisponivel(tmp_path, keyring_falso):
    keyring_falso(erro=RuntimeError("quebrou"))
    r = doctor.check_config(_cfg(tmp_path, CONFIG_OK.format(directory="/x")))
    k = next(c for c in r if c.name == "Keyring")
    assert k.level == WARN
    assert "quebrou" in k.detail


def test_check_directory_nao_definida():
    assert doctor.check_directory(None)[0].level == WARN
    assert doctor.check_directory("")[0].level == WARN


def test_check_directory_inexistente(tmp_path):
    r = doctor.check_directory(str(tmp_path / "x"))
    assert r[0].level == WARN
    assert "ainda não existe" in r[0].detail


def test_check_directory_limpa(tmp_path):
    r = doctor.check_directory(str(tmp_path))
    assert [c.level for c in r] == [PASS]


def test_check_directory_detecta_temporarios_e_pastas_presas(tmp_path):
    (tmp_path / "~tmp_a.flac").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "~tmp_b.flac").write_bytes(b"x")
    (tmp_path / "[IN PROGRESS] Album").mkdir()
    r = _niveis(doctor.check_directory(str(tmp_path)))
    assert r["Temporários"] == WARN
    assert r["Pastas [IN PROGRESS]"] == WARN
    det = [c.detail for c in doctor.check_directory(str(tmp_path))]
    assert any(d.startswith("2 arquivo") for d in det)


def test_check_directory_sem_permissao_de_escrita(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.os, "access", lambda *_a: False)
    r = _niveis(doctor.check_directory(str(tmp_path)))
    assert r["Escrita na pasta"] == FAIL


def test_check_directory_expande_til(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = doctor.check_directory("~")
    assert r[0].level == PASS


def _db_downloads(caminho, linhas=()):
    conn = sqlite3.connect(caminho)
    conn.execute("CREATE TABLE downloads (id TEXT, media_type TEXT, saved_path TEXT)")
    conn.executemany("INSERT INTO downloads VALUES (?,?,?)", linhas)
    conn.commit()
    conn.close()


def test_check_downloads_db_ausente(tmp_path):
    assert doctor.check_downloads_db(str(tmp_path / "a.db"))[0].level == WARN


def test_check_downloads_db_ok_e_registros_obsoletos(tmp_path):
    existe = tmp_path / "pasta"
    existe.mkdir()
    db = str(tmp_path / "d.db")
    _db_downloads(
        db,
        [
            ("1", "album", str(existe)),
            ("2", "album", str(tmp_path / "sumiu")),
            ("3", "track", ""),
        ],
    )
    r = doctor.check_downloads_db(db)
    assert r[0].level == PASS
    assert "3 registro" in r[0].detail
    assert r[1].name == "Registros obsoletos"
    assert r[1].detail.startswith("1 ")


def test_check_downloads_db_corrompido(tmp_path):
    db = tmp_path / "d.db"
    db.write_bytes(b"isto nao e sqlite" * 50)
    r = doctor.check_downloads_db(str(db))
    assert r[0].level == FAIL


def test_check_downloads_db_sem_tabela(tmp_path):
    db = tmp_path / "d.db"
    sqlite3.connect(db).close()
    assert doctor.check_downloads_db(str(db))[0].level == FAIL


def _db_lib(caminho, linhas=()):
    conn = sqlite3.connect(caminho)
    conn.execute(
        "CREATE TABLE albums (id TEXT, download_status TEXT, local_folder_path TEXT)"
    )
    conn.executemany("INSERT INTO albums VALUES (?,?,?)", linhas)
    conn.commit()
    conn.close()


def test_check_library_db_ausente(tmp_path):
    assert doctor.check_library_db(str(tmp_path / "l.db"))[0].level == WARN


def test_check_library_db_saudavel(tmp_path):
    pasta = tmp_path / "ok"
    pasta.mkdir()
    db = str(tmp_path / "l.db")
    _db_lib(db, [("1", "complete", str(pasta))])
    r = doctor.check_library_db(db)
    assert [c.level for c in r] == [PASS]
    assert "1 álbum" in r[0].detail


def test_check_library_db_todos_os_avisos(tmp_path):
    db = str(tmp_path / "l.db")
    _db_lib(
        db,
        [
            ("1", "queued", ""),
            ("2", "downloading", ""),
            ("3", "complete", ""),
            ("4", "complete", None),
            ("5", "complete", str(tmp_path / "sumiu")),
        ],
    )
    niveis = _niveis(doctor.check_library_db(db))
    assert niveis["Álbuns presos"] == WARN
    assert niveis["Sem pasta registrada"] == WARN
    assert niveis["Pasta sumiu"] == WARN


def test_check_library_db_corrompido(tmp_path):
    db = tmp_path / "l.db"
    db.write_bytes(b"lixo" * 100)
    assert doctor.check_library_db(str(db))[0].level == FAIL


def test_check_sentinels_sem_diretorio(tmp_path):
    assert doctor.check_sentinels(None) == []
    assert doctor.check_sentinels(str(tmp_path / "x")) == []


def _sentinela_falsa(monkeypatch, records, failures, identidade=None, validar=None):
    from qobuz_dl import sentinel

    monkeypatch.setattr(
        sentinel, "discover_sentinels", lambda _d: (records, failures, None)
    )
    monkeypatch.setattr(sentinel, "sentinel_identity", identidade or (lambda _p: "id"))
    monkeypatch.setattr(sentinel, "validate_folder", validar or (lambda _f, _p: []))


def test_check_sentinels_consistentes(tmp_path, monkeypatch):
    rec = SimpleNamespace(folder="f", payload={})
    _sentinela_falsa(monkeypatch, [rec, rec], [])
    r = doctor.check_sentinels(str(tmp_path))
    assert r == [Check(PASS, "Sentinelas", "2 álbum(ns) com sentinela consistente")]


def test_check_sentinels_com_problemas(tmp_path, monkeypatch):
    bons = SimpleNamespace(folder="f", payload={})
    ruim_validacao = SimpleNamespace(folder="g", payload={"x": 1})
    ruim_identidade = SimpleNamespace(folder="h", payload={"y": 1})

    def identidade(p):
        if p == {"y": 1}:
            raise ValueError("sem id")
        return "id"

    def validar(folder, _p):
        return ["erro"] if folder == "g" else []

    _sentinela_falsa(
        monkeypatch,
        [bons, ruim_validacao, ruim_identidade],
        ["falha-leitura"],
        identidade,
        validar,
    )
    r = doctor.check_sentinels(str(tmp_path))
    assert r[0].level == WARN
    assert "3 válida(s) lidas, 3 com problema" in r[0].detail


def test_resolve_directory(tmp_path):
    a = tmp_path / "a.ini"
    a.write_text("[qobuz]\ndirectory = /musica\n")
    b = tmp_path / "b.ini"
    b.write_text("[DEFAULT]\ndefault_folder = /legado\n")
    c = tmp_path / "c.ini"
    c.write_text("[qobuz]\nemail = x\n")
    ruim = tmp_path / "d.ini"
    ruim.write_text("nao e ini\n")
    assert doctor.resolve_directory(str(a)) == "/musica"
    assert doctor.resolve_directory(str(b)) == "/legado"
    assert doctor.resolve_directory(str(c)) is None
    assert doctor.resolve_directory(str(ruim)) is None
    assert doctor.resolve_directory(str(tmp_path / "nada.ini")) is None


def test_run_checks_agrega_todas_as_etapas(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "check_modules", lambda: [Check(PASS, "M")])
    monkeypatch.setattr(doctor, "check_binaries", lambda: [Check(PASS, "B")])
    monkeypatch.setattr(doctor, "check_sentinels", lambda _d: [Check(PASS, "S")])
    r = doctor.run_checks(
        config_file=str(tmp_path / "c.ini"),
        downloads_db=str(tmp_path / "d.db"),
        library_db=str(tmp_path / "l.db"),
        directory=str(tmp_path),
    )
    nomes = [c.name for c in r]
    assert nomes[0] == "Python"
    assert {"M", "B", "S", "config.ini", "qobuz_dl.db", "library.db"} <= set(nomes)


def test_render_codigo_de_saida(capsys):
    assert doctor.render([Check(PASS, "A", "ok"), Check(WARN, "B")]) == 0
    assert doctor.render([Check(FAIL, "C", "ruim")]) == 1
    saida = capsys.readouterr()
    assert "C" in (saida.out + saida.err)


def test_to_json():
    assert doctor.to_json([Check(WARN, "x", "y")]) == [
        {"level": "WARN", "name": "x", "detail": "y"}
    ]
