"""Utilitários de caminho compartilhados entre a CLI e a GUI.

Centraliza a validação/criação da pasta de biblioteca (a opção `directory`
do config.ini e da GUI) num único lugar, pra CLI e GUI nunca divergirem no
que "uma pasta válida" significa -- não importa se o programa foi
instalado via pip, pipx, venv ou dentro de um container Docker: o caminho
é sempre resolvido (`~`, relativos) e testado de verdade gravando e
apagando um arquivo de prova, não só lido dos bits de permissão do SO
(que podem enganar em SMB/NFS, ACLs do Windows etc.).
"""

from __future__ import annotations

import os
from pathlib import Path


class DirectoryNotUsable(ValueError):
    """Pasta não existe e não pôde ser criada, ou existe mas não é
    legível/gravável pelo usuário atual. Subclasse de ValueError de
    propósito: tanto qobuz_dl/webapp.py (rota /api/settings) quanto
    qobuz_dl/cli.py já sabem converter ValueError numa mensagem curta
    pro usuário, sem precisar de mais um bloco except em cada lugar."""


def ensure_directory_ready(raw_path: str) -> Path:
    """Resolve `raw_path` e garante que dá pra ler e escrever nela.

    - Se a pasta já existe: testa leitura E escrita de verdade (grava um
      arquivo de prova pequeno e apaga em seguida) -- não confia só em
      os.access(), que em alguns sistemas de arquivo de rede (SMB/NFS) ou
      com ACLs do Windows pode informar permissão que não existe de fato.
    - Se não existe: cria a pasta (e os pais que faltarem) e testa do
      mesmo jeito, deixando com as permissões padrão do processo (umask
      do sistema decide; não forçamos modo aberto).
    - Se o caminho existir e NÃO for uma pasta (por exemplo, for um
      arquivo comum), ou se a escrita falhar, levanta DirectoryNotUsable
      com uma mensagem pronta pra mostrar ao usuário -- CLI e GUI só
      precisam capturar isso e exibir `str(erro)`.

    Retorna o Path já resolvido (absoluto, `~` expandido) em caso de
    sucesso, pra quem chamou usar exatamente o mesmo valor que foi
    validado.
    """
    resolved = Path(os.path.expanduser(raw_path)).resolve()

    if resolved.exists() and not resolved.is_dir():
        raise DirectoryNotUsable(f"'{resolved}' já existe e não é uma pasta.")

    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        detail = error.strerror or str(error)
        raise DirectoryNotUsable(
            f"não foi possível criar '{resolved}': {detail}"
        ) from error

    probe = resolved / f".qobuz-dl-write-test-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        detail = error.strerror or str(error)
        raise DirectoryNotUsable(
            f"'{resolved}' existe, mas sem permissão de leitura/escrita: {detail}"
        ) from error

    return resolved
