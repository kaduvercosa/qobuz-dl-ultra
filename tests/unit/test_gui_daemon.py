"""Testes de cobertura total para qobuz_dl/gui_daemon.py."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import qobuz_dl.gui_daemon as gd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_state_path(tmp_path, monkeypatch):
    """Redireciona get_config_paths para tmp_path em todos os testes."""
    config_path = tmp_path / "config"
    config_path.mkdir()
    monkeypatch.setattr(
        gd,
        "get_config_paths",
        lambda: {"config_path": str(config_path)},
    )
    return config_path


# ---------------------------------------------------------------------------
# _read_state
# ---------------------------------------------------------------------------

def test_read_state_arquivo_ausente():
    assert gd._read_state() is None


def test_read_state_json_invalido(tmp_path, monkeypatch):
    config_path = tmp_path / "config2"
    config_path.mkdir()
    monkeypatch.setattr(gd, "get_config_paths", lambda: {"config_path": str(config_path)})
    (config_path / "gui.pid").write_text("nao-e-json", encoding="utf-8")
    assert gd._read_state() is None


def test_read_state_sem_pid(tmp_path, monkeypatch):
    config_path = tmp_path / "config3"
    config_path.mkdir()
    monkeypatch.setattr(gd, "get_config_paths", lambda: {"config_path": str(config_path)})
    (config_path / "gui.pid").write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
    assert gd._read_state() is None


def test_read_state_valido(tmp_path, monkeypatch):
    config_path = tmp_path / "config4"
    config_path.mkdir()
    monkeypatch.setattr(gd, "get_config_paths", lambda: {"config_path": str(config_path)})
    state = {"pid": 12345, "host": "127.0.0.1", "port": 8060}
    (config_path / "gui.pid").write_text(json.dumps(state), encoding="utf-8")
    resultado = gd._read_state()
    assert resultado["pid"] == 12345


# ---------------------------------------------------------------------------
# _process_alive
# ---------------------------------------------------------------------------

def test_process_alive_pid_inexistente():
    assert not gd._process_alive(9_999_999)


@pytest.mark.skipif(os.name == "nt", reason="sinal POSIX")
def test_process_alive_pid_proprio():
    assert gd._process_alive(os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_process_alive_windows_retorna_bool():
    resultado = gd._process_alive(os.getpid())
    assert isinstance(resultado, bool)


@pytest.mark.skipif(os.name == "nt", reason="sinal POSIX")
def test_process_alive_permission_error_retorna_true():
    with patch("os.kill", side_effect=PermissionError):
        assert gd._process_alive(1) is True


@pytest.mark.skipif(os.name == "nt", reason="sinal POSIX")
def test_process_alive_process_lookup_error_retorna_false():
    with patch("os.kill", side_effect=ProcessLookupError):
        assert gd._process_alive(1) is False


# ---------------------------------------------------------------------------
# detect_lan_ip
# ---------------------------------------------------------------------------

def test_detect_lan_ip_retorna_string_ou_none():
    resultado = gd.detect_lan_ip()
    assert resultado is None or isinstance(resultado, str)


def test_detect_lan_ip_oserror_retorna_none():
    fake_sock = MagicMock()
    fake_sock.connect.side_effect = OSError
    """Mocka o socket real que detect_lan_ip cria internamente."""
    fake_sock = MagicMock()
    fake_sock.connect.side_effect = OSError
    # socket.socket() retorna fake_sock
    with patch("socket.socket", return_value=fake_sock):
        resultado = gd.detect_lan_ip()
    assert resultado is None


def test_detect_lan_ip_retorna_ip_quando_connect_ok():
    fake_sock = MagicMock()
    fake_sock.getsockname.return_value = ("192.168.1.10", 0)
    with patch("socket.socket", return_value=fake_sock):
        resultado = gd.detect_lan_ip()
    assert resultado == "192.168.1.10"


# ---------------------------------------------------------------------------
# resolve_host
# ---------------------------------------------------------------------------

def test_resolve_host_passthrough():
    assert gd.resolve_host("127.0.0.1") == "127.0.0.1"
    assert gd.resolve_host("0.0.0.0") == "0.0.0.0"


def test_resolve_host_lan_com_ip_detectado():
    with patch.object(gd, "detect_lan_ip", return_value="192.168.1.50"):
        assert gd.resolve_host("lan") == "192.168.1.50"


def test_resolve_host_lan_sem_ip_levanta():
    with patch.object(gd, "detect_lan_ip", return_value=None):
        with pytest.raises(ValueError, match="IP de rede local"):
            gd.resolve_host("lan")


# ---------------------------------------------------------------------------
# validate_host
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "0.0.0.0"])
def test_validate_host_loopback_e_any(host):
    assert gd.validate_host(host) == host


@pytest.mark.parametrize("host", ["192.168.1.100", "10.0.0.1", "::ffff:192.0.2.1"])
def test_validate_host_ip_valido(host):
    assert gd.validate_host(host) == host


def test_validate_host_invalido_levanta():
    with pytest.raises(ValueError, match="host inv"):
    with pytest.raises(ValueError, match="host inválido"):
        gd.validate_host("nao-e-um-host")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_sem_arquivo_retorna_nao_rodando():
    resultado = gd.status()
    assert resultado == {"running": False}


def test_status_processo_morto_retorna_nao_rodando():
    with patch.object(gd, "_read_state", return_value={"pid": 9_999_999, "host": "127.0.0.1"}):
        with patch.object(gd, "_process_alive", return_value=False):
            resultado = gd.status()
    assert resultado["running"] is False


def test_status_processo_vivo_retorna_estado():
    state = {"pid": 123, "host": "127.0.0.1", "port": 8060}
    with patch.object(gd, "_read_state", return_value=state):
        with patch.object(gd, "_process_alive", return_value=True):
            resultado = gd.status()
    assert resultado["running"] is True
    assert resultado["pid"] == 123


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

def test_stop_sem_processo_retorna_false():
    with patch.object(gd, "_read_state", return_value=None):
        assert gd.stop() is False


def test_stop_processo_morto_retorna_false():
    with patch.object(gd, "_read_state", return_value={"pid": 9_999_999}):
        with patch.object(gd, "_process_alive", return_value=False):
            assert gd.stop() is False


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM POSIX")
def test_stop_envia_sigterm_e_retorna_true():
    # Chamadas a _process_alive em stop():
    #   1: if not _process_alive(pid)  -> True  (entra no bloco)
    #   2: while _process_alive(pid)   -> True  (loop executa 1x)
    #   3: while _process_alive(pid)   -> False (sai do while)
    #   4: if _process_alive(pid)      -> False (nao manda SIGKILL)
    with patch.object(gd, "_read_state", return_value={"pid": 555}):
        with patch.object(gd, "_process_alive", side_effect=[True, True, False, False]):
            with patch("qobuz_dl.gui_daemon.os.kill") as mock_kill:
                with patch("qobuz_dl.gui_daemon.time.sleep"):
                    with patch(
                        "qobuz_dl.gui_daemon.time.monotonic",
                        side_effect=[0.0, 0.0, 0.1, 0.1],
                    ):
                        resultado = gd.stop(timeout=0.5)
    with patch.object(gd, "_read_state", return_value={"pid": 555}):
        # [True] = entra no bloco stop; [True, False] = loop while
        with patch.object(gd, "_process_alive", side_effect=[True, True, False]):
            with patch("qobuz_dl.gui_daemon.os.kill") as mock_kill:
                with patch("qobuz_dl.gui_daemon.time.sleep"):
                    resultado = gd.stop(timeout=0.5)
    assert resultado is True
    mock_kill.assert_any_call(555, signal.SIGTERM)


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL POSIX")
def test_stop_envia_sigkill_se_processo_nao_termina():
    with patch.object(gd, "_read_state", return_value={"pid": 666}):
        with patch.object(gd, "_process_alive", return_value=True):
            with patch("qobuz_dl.gui_daemon.os.kill") as mock_kill:
                with patch("qobuz_dl.gui_daemon.time.sleep"):
                    with patch(
                        "qobuz_dl.gui_daemon.time.monotonic",
                        side_effect=[0.0, 0.0, 100.0, 100.0],
                    ):
                        resultado = gd.stop(timeout=0.01)
    assert resultado is True
    kill_calls = [c.args[1] for c in mock_kill.call_args_list]
    assert signal.SIGKILL in kill_calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX")
def test_stop_process_lookup_error_ao_matar():
    # Chamadas a _process_alive em stop():
    #   1: if not _process_alive(pid) -> True  (entra)
    #   os.kill(SIGTERM) levanta ProcessLookupError -> capturado
    #   2: while _process_alive(pid)  -> False (sai do loop)
    #   3: if _process_alive(pid)     -> False (nao SIGKILL)
    with patch.object(gd, "_read_state", return_value={"pid": 777}):
        with patch.object(gd, "_process_alive", side_effect=[True, False, False]):
            with patch("qobuz_dl.gui_daemon.os.kill", side_effect=ProcessLookupError):
                with patch(
                    "qobuz_dl.gui_daemon.time.monotonic",
                    side_effect=[0.0, 0.0],
                ):
                    resultado = gd.stop(timeout=0.1)
    with patch.object(gd, "_read_state", return_value={"pid": 777}):
        with patch.object(gd, "_process_alive", side_effect=[True, False]):
            with patch("qobuz_dl.gui_daemon.os.kill", side_effect=ProcessLookupError):
                resultado = gd.stop(timeout=0.1)
    assert resultado is True


# ---------------------------------------------------------------------------
# _tail_log
# ---------------------------------------------------------------------------

def test_tail_log_arquivo_ausente():
    assert gd._tail_log(Path("/caminho/que/nao/existe/gui.log")) == ""


def test_tail_log_retorna_ultimas_linhas(tmp_path):
    log = tmp_path / "test.log"
    log.write_text("\n".join(str(i) for i in range(20)), encoding="utf-8")
    resultado = gd._tail_log(log, lines=4)
    linhas = resultado.splitlines()
    assert len(linhas) == 4
    assert linhas[-1] == "19"


# ---------------------------------------------------------------------------
# _wait_for_startup
# ---------------------------------------------------------------------------

def test_wait_for_startup_porta_respondendo(tmp_path):
    proc = MagicMock()
    proc.poll.return_value = None
    log = tmp_path / "gui.log"
    log.write_text("", encoding="utf-8")
    proc.poll.return_value = None  # processo vivo
    log = tmp_path / "gui.log"
    log.write_text("", encoding="utf-8")
    # create_connection retorna context manager que não levanta
    fake_conn = MagicMock()
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)
    with patch("qobuz_dl.gui_daemon.socket.create_connection", return_value=fake_conn):
        gd._wait_for_startup(proc, "127.0.0.1", 8060, log, timeout=2.0)


def test_wait_for_startup_processo_morreu(tmp_path):
    proc = MagicMock()
    proc.poll.return_value = 1
    log = tmp_path / "gui.log"
    log.write_text("Traceback...\nerro fatal", encoding="utf-8")
    with pytest.raises(RuntimeError, match="saiu com c"):
    proc.poll.return_value = 1  # saiu com erro
    log = tmp_path / "gui.log"
    log.write_text("Traceback...\nerro fatal", encoding="utf-8")
    with pytest.raises(RuntimeError, match="saiu com código 1"):
        gd._wait_for_startup(proc, "127.0.0.1", 8060, log, timeout=0.1)


def test_wait_for_startup_timeout_sem_falha(tmp_path):
    """Prazo esgota com processo vivo — deve retornar sem levantar."""
    proc = MagicMock()
    proc.poll.return_value = None
    log = tmp_path / "gui.log"
    log.write_text("", encoding="utf-8")
    with patch("qobuz_dl.gui_daemon.socket.create_connection", side_effect=OSError):
        with patch("qobuz_dl.gui_daemon.time.sleep"):
            with patch("qobuz_dl.gui_daemon.time.monotonic", side_effect=[0.0, 100.0]):
                gd._wait_for_startup(proc, "127.0.0.1", 8060, log, timeout=0.0)


def test_wait_for_startup_host_0000_usa_loopback(tmp_path):
    proc = MagicMock()
    proc.poll.return_value = None
    log = tmp_path / "gui.log"
    log.write_text("", encoding="utf-8")
    fake_conn = MagicMock()
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)
    with patch("qobuz_dl.gui_daemon.socket.create_connection", return_value=fake_conn) as mock_cc:
        gd._wait_for_startup(proc, "0.0.0.0", 8060, log, timeout=2.0)
        args = mock_cc.call_args[0][0]
        assert args[0] == "127.0.0.1"


# ---------------------------------------------------------------------------
# detect_public_ip
# ---------------------------------------------------------------------------

def test_detect_public_ip_retorna_none_fora_de_nuvem():
    with patch("urllib.request.urlopen", side_effect=OSError):
        resultado = gd.detect_public_ip()
    assert resultado is None


def test_detect_public_ip_ip_invalido_retorna_none():
    """urlopen é chamado duas vezes: uma pro token, outra pro IP."""
    token_resp = MagicMock()
    token_resp.read.return_value = b"token123"
    ip_resp = MagicMock()
    ip_resp.read.return_value = b"nao-e-um-ip"
    with patch("urllib.request.urlopen", side_effect=[token_resp, ip_resp]):
        resultado = gd.detect_public_ip()
    assert resultado is None


def test_detect_public_ip_retorna_ip_valido():
    token_resp = MagicMock()
    token_resp.read.return_value = b"token123"
    ip_resp = MagicMock()
    ip_resp.read.return_value = b"54.12.34.56"
    with patch("urllib.request.urlopen", side_effect=[token_resp, ip_resp]):
        resultado = gd.detect_public_ip()
    assert resultado == "54.12.34.56"


def test_detect_public_ip_segundo_urlopen_levanta_oserror():
    """Erro na segunda chamada (leitura do IP) deve retornar None."""
    token_resp = MagicMock()
    token_resp.read.return_value = b"token123"
    with patch("urllib.request.urlopen", side_effect=[token_resp, OSError]):
        resultado = gd.detect_public_ip()
    assert resultado is None


# ---------------------------------------------------------------------------
# display_url
# ---------------------------------------------------------------------------

def test_display_url_loopback():
    url = gd.display_url("127.0.0.1", 8060)
    assert url == "http://127.0.0.1:8060/"


def test_display_url_localhost():
    # localhost está em LOOPBACK_HOSTS mas não é um ip_address válido;
    # validate_host aceita, display_url não deve tentar ip_address nele
    url = gd.display_url("localhost", 8060)
    assert "localhost" in url


def test_display_url_0000_usa_lan_ou_loopback():
    with patch.object(gd, "detect_public_ip", return_value=None):
        with patch.object(gd, "detect_lan_ip", return_value="192.168.1.99"):
            url = gd.display_url("0.0.0.0", 8080)
    assert "192.168.1.99" in url


def test_display_url_ip_privado_prefere_publico():
    with patch.object(gd, "detect_public_ip", return_value="54.0.0.1"):
        url = gd.display_url("192.168.1.50", 8060)
    assert "54.0.0.1" in url


def test_display_url_ip_privado_sem_publico_usa_proprio():
    with patch.object(gd, "detect_public_ip", return_value=None):
        url = gd.display_url("192.168.1.50", 8060)
    assert "192.168.1.50" in url


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def test_start_ja_rodando_retorna_already_running():
    with patch.object(gd, "status", return_value={"running": True, "pid": 99}):
        resultado = gd.start()
    assert resultado["alreadyRunning"] is True


def test_start_porta_invalida_levanta():
    with patch.object(gd, "status", return_value={"running": False}):
        with pytest.raises(ValueError, match="entre 1 e 65535"):
            gd.start(port=0)


def test_start_porta_muito_alta_levanta():
    with patch.object(gd, "status", return_value={"running": False}):
        with pytest.raises(ValueError, match="entre 1 e 65535"):
            gd.start(port=99999)


def test_start_sobe_processo_e_retorna_estado():
    mock_proc = MagicMock()
    mock_proc.pid = 4242
    with (
        patch.object(gd, "status", return_value={"running": False}),
        patch.object(gd, "resolve_host", return_value="127.0.0.1"),
        patch.object(gd, "validate_host", return_value="127.0.0.1"),
        patch("qobuz_dl.gui_daemon.subprocess.Popen", return_value=mock_proc),
        patch.object(gd, "_wait_for_startup"),
    ):
        resultado = gd.start(host="127.0.0.1", port=8060, open_browser=False)
    assert resultado["running"] is True
    assert resultado["pid"] == 4242
    assert resultado["alreadyRunning"] is False


def test_start_com_demo_adiciona_flag():
    mock_proc = MagicMock()
    mock_proc.pid = 5050
    with (
        patch.object(gd, "status", return_value={"running": False}),
        patch.object(gd, "resolve_host", return_value="127.0.0.1"),
        patch.object(gd, "validate_host", return_value="127.0.0.1"),
        patch("qobuz_dl.gui_daemon.subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch.object(gd, "_wait_for_startup"),
    ):
        gd.start(host="127.0.0.1", port=8060, demo=True, open_browser=False)
    argv = mock_popen.call_args[0][0]
    assert "--demo" in argv


def test_start_wait_falha_apaga_state_e_relanca():
    mock_proc = MagicMock()
    mock_proc.pid = 6060
    with (
        patch.object(gd, "status", return_value={"running": False}),
        patch.object(gd, "resolve_host", return_value="127.0.0.1"),
        patch.object(gd, "validate_host", return_value="127.0.0.1"),
        patch("qobuz_dl.gui_daemon.subprocess.Popen", return_value=mock_proc),
        patch.object(gd, "_wait_for_startup", side_effect=RuntimeError("nao subiu")),
    ):
        with pytest.raises(RuntimeError, match="nao subiu"):
            gd.start(host="127.0.0.1", port=8060, open_browser=False)
    assert not gd._state_path().exists()


def test_start_abre_browser_loopback():
    mock_proc = MagicMock()
    mock_proc.pid = 7070
    # webbrowser é importado lazy dentro de start(); patch no módulo raiz
    with (
        patch.object(gd, "status", return_value={"running": False}),
        patch.object(gd, "resolve_host", return_value="127.0.0.1"),
        patch.object(gd, "validate_host", return_value="127.0.0.1"),
        patch("qobuz_dl.gui_daemon.subprocess.Popen", return_value=mock_proc),
        patch.object(gd, "_wait_for_startup"),
        patch("webbrowser.open") as mock_browser,
    ):
        gd.start(host="127.0.0.1", port=8060, demo=False, open_browser=True)
    mock_browser.assert_called_once()


def test_start_nao_abre_browser_em_modo_demo():
    mock_proc = MagicMock()
    mock_proc.pid = 8080
    with (
        patch.object(gd, "status", return_value={"running": False}),
        patch.object(gd, "resolve_host", return_value="127.0.0.1"),
        patch.object(gd, "validate_host", return_value="127.0.0.1"),
        patch("qobuz_dl.gui_daemon.subprocess.Popen", return_value=mock_proc),
        patch.object(gd, "_wait_for_startup"),
        patch("webbrowser.open") as mock_browser,
    ):
        gd.start(host="127.0.0.1", port=8060, demo=True, open_browser=True)
    mock_browser.assert_not_called()

