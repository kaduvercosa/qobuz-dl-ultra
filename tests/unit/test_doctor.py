"""Testes do `qobuz-dl doctor` (somente leitura)."""

import os
import sqlite3

from qobuz_dl import doctor
from qobuz_dl.library_db import LibraryDB


def _niveis(results, nome):
    return [r.level for r in results if r.name == nome]


def test_config_ausente_e_falha(tmp_path):
    assert doctor.check_config(str(tmp_path / "nao.ini"))[0].level == doctor.FAIL


def test_config_completo_e_permissao(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[qobuz]\nemail=a\napp_id=1\nsecrets=x\ndefault_quality=27\ndisable_keyring=true\n"
    )
    if os.name == "posix":
        os.chmod(cfg, 0o644)
    res = doctor.check_config(str(cfg))
    if os.name == "posix":
        assert _niveis(res, "Permissão do config") == [doctor.WARN]
    assert not any(r.level == doctor.FAIL for r in res)


def test_config_sem_campos_obrigatorios(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[qobuz]\nemail=a\n")
    falhas = [r.name for r in doctor.check_config(str(cfg)) if r.level == doctor.FAIL]
    assert "config: app_id" in falhas and "config: secrets" in falhas


def test_config_nao_vaza_segredo(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[qobuz]\nemail=a\napp_id=1\nsecrets=x\ndefault_quality=27\n"
        "disable_keyring=true\nauth_token=SEGREDO123\n"
    )
    texto = " ".join(f"{r.name} {r.detail}" for r in doctor.check_config(str(cfg)))
    assert "SEGREDO123" not in texto


def test_directory_temporarios(tmp_path):
    (tmp_path / "[IN PROGRESS] X").mkdir()
    (tmp_path / "~tmp_1.flac").write_bytes(b"x")
    nomes = [r.name for r in doctor.check_directory(str(tmp_path))]
    assert "Temporários" in nomes and "Pastas [IN PROGRESS]" in nomes


def test_downloads_db_integro_e_obsoleto(tmp_path):
    db = tmp_path / "q.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE downloads (id TEXT, media_type TEXT, saved_path TEXT)")
    conn.execute("INSERT INTO downloads VALUES ('1','album','/nao/existe')")
    conn.commit()
    conn.close()
    res = doctor.check_downloads_db(str(db))
    assert res[0].level == doctor.PASS
    assert any(r.name == "Registros obsoletos" for r in res)


def test_downloads_db_corrompido(tmp_path):
    db = tmp_path / "q.db"
    db.write_bytes(b"isto nao e sqlite" * 50)
    assert doctor.check_downloads_db(str(db))[0].level == doctor.FAIL


def test_library_db_presos_e_sem_pasta(tmp_path):
    lib = LibraryDB(tmp_path / "l.db")
    a = lib.upsert_album("qobuz", "1", "T", "A")
    lib.update_status(a, "downloading")
    b = lib.upsert_album("qobuz", "2", "T", "A")
    lib.set_download_state(b, downloaded=True)
    nomes = [r.name for r in doctor.check_library_db(lib.path)]
    assert "Álbuns presos" in nomes and "Sem pasta registrada" in nomes


def test_render_codigo_de_saida():
    assert doctor.render([doctor.Check(doctor.PASS, "x")]) == 0
    assert doctor.render([doctor.Check(doctor.WARN, "x")]) == 0
    assert doctor.render([doctor.Check(doctor.FAIL, "x")]) == 1


def test_resolve_directory(tmp_path):
    cfg = tmp_path / "c.ini"
    cfg.write_text("[qobuz]\ndirectory=/musica\n")
    assert doctor.resolve_directory(str(cfg)) == "/musica"
    assert doctor.resolve_directory(str(tmp_path / "nao")) is None
