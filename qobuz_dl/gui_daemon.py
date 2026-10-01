"""Controla a GUI (Qobuz Studio) como um processo em segundo plano.

`start()`/`stop()`/`status()` são o que dá a `qobuz-dl gui` a capacidade de
ligar/desligar sem prender o terminal: `start()` sobe um processo FILHO
destacado (`qobuz-dl gui run` por baixo) e devolve o controle na hora;
`stop()` derruba esse filho pelo PID guardado. `run()` -- o modo em
primeiro plano, bloqueante -- mora em qobuz_dl/webapp.py (`run_gui`),
não aqui: é o que tanto o processo filho quanto quem prefere rodar em
primeiro plano de propósito (ex.: o ENTRYPOINT do Dockerfile, onde o
container inteiro deve morrer se o servidor cair) usam diretamente.

Sem dependências fora da biblioteca padrão de propósito -- assim
`qobuz-dl gui stop`/`status` funcionam mesmo se o extra opcional `[gui]`
(FastAPI/Uvicorn) não estiver instalado nessa hora.
"""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from qobuz_dl.utils import get_config_paths

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ANY_INTERFACE_HOST = "0.0.0.0"


def _state_path() -> Path:
    return Path(get_config_paths()["config_path"]) / "gui.pid"


def _log_path() -> Path:
    return Path(get_config_paths()["config_path"]) / "gui.log"


def _read_state() -> dict[str, Any] | None:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return None
    return data


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in out.stdout
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, só pertence a outro usuário -- ainda está vivo
    return True


def detect_lan_ip() -> str | None:
    """Descobre o IP da interface usada pra sair pra rede/internet, sem
    de fato mandar nenhum pacote (socket UDP só usado pra perguntar ao
    sistema operacional qual seria a rota, nunca chega a conectar)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def resolve_host(host: str) -> str:
    """Resolve o valor especial 'lan' pro IP real desta máquina na rede
    local. Qualquer outro valor volta como foi passado -- validar se é
    um host aceitável é responsabilidade de quem chama (ver
    `validate_host` abaixo), não desta função."""
    if host.strip().lower() == "lan":
        detected = detect_lan_ip()
        if not detected:
            raise ValueError(
                "não consegui detectar um IP de rede local nesta máquina; "
                "informe o IP manualmente com --host"
            )
        return detected
    return host


def validate_host(host: str) -> str:
    """Aceita loopback, 0.0.0.0 (todas as interfaces -- pensado pra
    Docker/preview isolado) ou qualquer IPv4/IPv6 válido (a pessoa está
    pedindo de propósito pra expor na rede local, ex.: --host lan já
    resolvido, ou o IP de um NAS). Recusa só o que claramente não é um
    host utilizável -- um domínio digitado errado, texto solto etc."""
    if host in LOOPBACK_HOSTS or host == _ANY_INTERFACE_HOST:
        return host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            f"host inválido: '{host}' (use 127.0.0.1, 0.0.0.0, lan, ou um IP válido)"
        ) from None
    return host


def status() -> dict[str, Any]:
    state = _read_state()
    if not state or not _process_alive(state["pid"]):
        if state:
            _state_path().unlink(missing_ok=True)
        return {"running": False}
    return {"running": True, **state}


def stop(timeout: float = 8.0) -> bool:
    """Encerra o processo em segundo plano, se houver um rodando.
    Retorna False se já não havia nada pra parar."""
    state = _read_state()
    if not state or not _process_alive(state["pid"]):
        _state_path().unlink(missing_ok=True)
        return False

    pid = state["pid"]
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _process_alive(pid):
            time.sleep(0.2)
        if _process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    _state_path().unlink(missing_ok=True)
    return True


def _tail_log(log_path: Path, lines: int = 6) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])


def _wait_for_startup(
    process: subprocess.Popen,
    host: str,
    port: int,
    log_path: Path,
    timeout: float = 6.0,
) -> None:
    """Espera o processo filho subir de verdade, em vez de só dar uma
    dormida fixa e torcer -- uma dormida curta pode confirmar "vivo"
    bem antes do processo terminar de importar tudo e falhar de verdade
    (foi exatamente o que aconteceu num teste manual: uma dormida fixa
    dizia "subiu" pra um processo que já tinha morrido). Usa
    `process.poll()` (não `os.kill(pid, 0)`) porque este é um filho
    DIRETO nosso -- um filho que já saiu mas ainda não foi "colhido"
    (esperado com wait/poll) fica zumbi e `os.kill(pid, 0)` continua
    enxergando ele como vivo; `poll()` resolve isso certo. Confirma as
    duas coisas em loop: o processo continua rodando E a porta já
    aceita conexão. Levanta RuntimeError com o fim do log se o processo
    morrer antes disso; se o prazo esgotar sem confirmar nem sucesso
    nem morte, deixa rodando (processo ainda vivo, só mais lento que o
    normal pra subir -- ex.: primeira execução compilando bytecode)."""
    deadline = time.monotonic() + timeout
    probe_host = (
        "127.0.0.1" if host in LOOPBACK_HOSTS or host == _ANY_INTERFACE_HOST else host
    )
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            tail = _tail_log(log_path)
            detail = f"\n{tail}" if tail else ""
            raise RuntimeError(
                f"o servidor não subiu (saiu com código {exit_code}; log em {log_path}){detail}"
            )
        try:
            with socket.create_connection((probe_host, port), timeout=0.3):
                return  # porta respondeu -- subiu de verdade
        except OSError:
            time.sleep(0.2)


def detect_public_ip() -> str | None:
    """IP público quando a máquina está numa nuvem com NAT (ex.: AWS EC2),
    onde a interface de rede só enxerga o IP privado (172.31.x.x). Pergunta
    ao serviço de metadados da própria instância (169.254.169.254, link-local:
    não sai pra internet). Fora de uma nuvem isso simplesmente falha rápido."""
    import urllib.request

    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urllib.request.urlopen(token_req, timeout=0.4).read().decode()
        ip_req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/public-ipv4",
            headers={"X-aws-ec2-metadata-token": token},
        )
        ip = urllib.request.urlopen(ip_req, timeout=0.4).read().decode().strip()
        ipaddress.ip_address(ip)
        return ip
    except (OSError, ValueError):
        return None


def display_url(host: str, port: int) -> str:
    """URL que a pessoa realmente digita no navegador. 0.0.0.0 não é um
    endereço acessível; e num servidor em nuvem o IP da interface é o
    privado, então prefere o IP público quando dá pra descobrir."""
    shown = host
    is_private = False
    try:
        addr = ipaddress.ip_address(host)
        is_private = addr.is_private and not addr.is_loopback
    except ValueError:
        pass
    if host == _ANY_INTERFACE_HOST or is_private:
        shown = (
            detect_public_ip()
            or (detect_lan_ip() if host == _ANY_INTERFACE_HOST else host)
            or "127.0.0.1"
        )
    return f"http://{shown}:{port}/"


def start(
    host: str = "127.0.0.1",
    port: int = 8060,
    demo: bool = False,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Sobe a GUI em segundo plano (processo destacado) e devolve o
    controle imediatamente -- o terminal continua livre pros outros
    comandos. Se já tiver uma instância rodando, apenas informa o estado
    dela em vez de subir uma segunda."""
    existing = status()
    if existing["running"]:
        return {**existing, "alreadyRunning": True}

    if not 1 <= port <= 65535:
        raise ValueError("a porta precisa estar entre 1 e 65535")
    resolved_host = validate_host(resolve_host(host))

    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_path.parent.chmod(0o700)
    except OSError:
        pass

    argv = [
        sys.executable,
        "-m",
        "qobuz_dl",
        "gui",
        "run",
        "--host",
        resolved_host,
        "--port",
        str(port),
        "--no-browser",
    ]
    if demo:
        argv.append("--demo")

    popen_kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    with open(log_path, "a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            argv, stdout=log_file, stderr=log_file, **popen_kwargs
        )

    state = {
        "pid": process.pid,
        "host": resolved_host,
        "port": port,
        "demo": demo,
        "startedAt": time.time(),
        "log": str(log_path),
    }
    _state_path().write_text(json.dumps(state), encoding="utf-8")
    try:
        _state_path().chmod(0o600)
    except OSError:
        pass

    # Confirma de verdade que o servidor subiu (processo vivo + porta
    # respondendo) antes de devolver o terminal -- não só uma dormida fixa.
    try:
        _wait_for_startup(process, resolved_host, port, log_path)
    except RuntimeError:
        _state_path().unlink(missing_ok=True)
        raise

    if (
        open_browser
        and not demo
        and (resolved_host in LOOPBACK_HOSTS or resolved_host == _ANY_INTERFACE_HOST)
    ):
        import webbrowser

        local = "127.0.0.1" if resolved_host == _ANY_INTERFACE_HOST else resolved_host
        webbrowser.open(f"http://{local}:{port}/")

    return {"running": True, "alreadyRunning": False, **state}
