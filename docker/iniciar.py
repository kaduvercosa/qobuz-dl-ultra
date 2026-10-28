#!/usr/bin/env python3
"""Ponto de entrada do container do qobuz-dl-ultra.

Roda DENTRO da imagem, antes do programa, e cuida do que costuma dar errado
no Docker. O assistente de configuração é o do próprio programa:

  1. confirma que as pastas de config e de downloads aceitam escrita;
  2. se não existe config.ini, abre o assistente normal (`-r`);
  3. confere o config.ini e conserta o que quebraria no container:
       - Keyring ligado (o container não tem chaveiro do sistema);
       - pasta de download fora da pasta montada (os arquivos sumiriam
         junto com o container);
     guardando antes uma cópia em config.ini.bak;
  4. executa o programa com os argumentos que você passou.

Pode rodar a cada comando: se está tudo certo, não imprime nada.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
import tempfile
from typing import NoReturn

# Pasta de downloads DENTRO do container; o docker-compose.yml monta a pasta
# do host exatamente aqui.
MUSIC_DIR = "/home/qobuz/QobuzDownloads"

OBRIGATORIAS = ("email", "app_id", "secrets", "default_limit", "default_quality")

# Faz qualquer tentativa de usar o Keyring falhar de forma previsível: o
# programa então guarda o token no config.ini (protegido por chmod 600), em vez
# de "salvar" num cofre que some junto com o container.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def log(msg: str) -> None:
    print(msg, flush=True)


def erro(msg: str) -> NoReturn:
    print(f"\n[ERRO] {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def checar_pasta_gravavel(caminho: str, descricao: str) -> None:
    """Grava e apaga um arquivo de prova (não confia só nos bits de permissão)."""
    try:
        os.makedirs(caminho, exist_ok=True)
        prova = os.path.join(caminho, f".qobuz-dl-write-test-{os.getpid()}")
        with open(prova, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(prova)
    except OSError as e:
        erro(
            f"Sem permissão para gravar em {descricao} ({caminho}): "
            f"{e.strerror or e}.\n"
            "       Rode o comando de novo (o serviço 'permissoes' tenta corrigir "
            "o dono da pasta). Em NAS/SMB/NFS ou Docker Desktop, libere leitura "
            "e escrita na pasta compartilhada para o usuário da imagem "
            "(normalmente UID 1000)."
        )


def gravar_config(config: configparser.ConfigParser, destino: str) -> None:
    """Grava atomicamente com permissão 0600 (mesma regra do programa)."""
    pasta = os.path.dirname(os.path.abspath(destino))
    fd, temporario = tempfile.mkstemp(prefix=".config.", dir=pasta, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            config.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporario, destino)
        os.chmod(destino, 0o600)
    except BaseException:
        try:
            os.remove(temporario)
        except OSError:
            pass
        raise


def fazer_backup(config_file: str) -> None:
    backup = config_file + ".bak"
    shutil.copy2(config_file, backup)
    os.chmod(backup, 0o600)
    log(f"[i] Cópia de segurança do config anterior: {backup}")


def dentro_da_pasta_de_musica(valor: str) -> bool:
    caminho = os.path.normpath(os.path.expanduser(valor))
    return caminho == MUSIC_DIR or caminho.startswith(MUSIC_DIR + os.sep)


def rodar_assistente() -> None:
    """Abre o assistente do próprio programa (python -m qobuz_dl -r)."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        erro(
            "Ainda não existe configuração e o assistente precisa de um terminal.\n"
            "       Rode sem -T:  docker compose run --rm qobuz-dl -r"
        )
    log(
        "\n[i] Primeira execução: vou abrir o assistente de configuração.\n"
        "    - Pasta de download: pode apertar Enter. Depois eu aponto "
        "para a pasta montada.\n"
        "    - Keyring: tanto faz a resposta. Nesta imagem ele fica desativado "
        "e o token\n      é guardado no config.ini (permissão 600).\n"
    )
    codigo = subprocess.run([sys.executable, "-m", "qobuz_dl", "-r"]).returncode
    if codigo != 0:
        erro(
            f"O assistente terminou com erro (código {codigo}). Rode o comando de novo."
        )


def consertar(config_file: str) -> str:
    """Retorna 'ok', 'invalido' (precisa refazer) ou 'sem_token'."""
    if not os.path.isfile(config_file):
        return "invalido"
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(config_file, encoding="utf-8")
    except configparser.Error:
        return "invalido"
    if not cfg.has_section("qobuz"):
        return "invalido"
    q = cfg["qobuz"]
    if any(not q.get(k, "").strip() for k in OBRIGATORIAS):
        return "invalido"

    # Sem Keyring o programa lê o token de auth_token (ou de password).
    if not (q.get("auth_token", "").strip() or q.get("password", "").strip()):
        return "sem_token"

    mudancas = []
    if q.get("disable_keyring", "false").strip().lower() not in {
        "true",
        "yes",
        "y",
        "1",
    }:
        q["disable_keyring"] = "true"
        mudancas.append("disable_keyring = true (o container não tem Keyring)")
    if not dentro_da_pasta_de_musica(q.get("directory", "")):
        anterior = q.get("directory", "(ausente)")
        q["directory"] = MUSIC_DIR
        mudancas.append(f"directory = {MUSIC_DIR} (antes: {anterior})")

    if mudancas:
        fazer_backup(config_file)
        gravar_config(cfg, config_file)
        for m in mudancas:
            log(f"[+] Ajustado no config.ini: {m}")
    return "ok"


def garantir_config(config_file: str) -> None:
    resultado = consertar(config_file)
    if resultado == "ok":
        return
    if resultado == "sem_token":
        erro(
            "O config.ini existe, mas sem o token (provavelmente foi criado em outra "
            "máquina com Keyring).\n       Refaça a configuração aqui:  "
            "docker compose run --rm qobuz-dl -r"
        )
    # inválido ou inexistente
    if os.path.isfile(config_file):
        log("[!] O config.ini existente está incompleto ou corrompido; vou refazer.")
        fazer_backup(config_file)
        os.remove(config_file)
    rodar_assistente()
    resultado = consertar(config_file)
    if resultado != "ok":
        erro(
            "A configuração não ficou completa (assistente interrompido?).\n"
            "       Rode o comando de novo para refazer."
        )


def main() -> None:
    try:
        from qobuz_dl.paths import DirectoryNotUsable, ensure_directory_ready
        from qobuz_dl.utils import get_config_paths
    except ImportError as e:
        erro(
            f"Este script deve rodar dentro da imagem do qobuz-dl-ultra ({e}).\n"
            "       Use: docker compose run --rm qobuz-dl <comando>"
        )

    caminhos = get_config_paths()
    config_path, config_file = caminhos["config_path"], caminhos["config_file"]

    checar_pasta_gravavel(config_path, "a pasta de configuração")
    try:
        ensure_directory_ready(MUSIC_DIR)
    except DirectoryNotUsable as e:
        erro(
            f"Pasta de downloads inutilizável: {e}\n"
            "       Rode o comando de novo (o serviço 'permissoes' tenta corrigir "
            "o dono) ou libere escrita na pasta do host."
        )

    args = sys.argv[1:]

    if "-r" in args or "--reset" in args:
        # O usuário pediu para refazer a configuração: deixa o programa fazer
        # e depois aplica os ajustes do Docker.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            erro("O assistente precisa de um terminal. Rode sem -T.")
        codigo = subprocess.run([sys.executable, "-m", "qobuz_dl", *args]).returncode
        if codigo == 0:
            garantir_config(config_file)
        sys.exit(codigo)

    garantir_config(config_file)
    os.execv(sys.executable, [sys.executable, "-m", "qobuz_dl", *args])


if __name__ == "__main__":
    main()
