"""Utilitários de caminho usados pela CLI.

Centraliza a validação/criação da pasta de biblioteca (a opção `directory`
do config.ini) num único lugar -- não importa se o programa foi
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
    propósito: qobuz_dl/cli.py já sabe converter ValueError numa mensagem
    curta pro usuário, sem precisar de mais um bloco except."""


def resolve_library_directory(
    explicit_directory: str | os.PathLike[str] | None = None,
    configured_directory: str | os.PathLike[str] | None = None,
    *,
    ios_home: str | None = None,
    home: str | None = None,
    windows: bool | None = None,
) -> str:
    """Resolve the single library directory used by directory-based commands.

    Precedence is explicit CLI path, then ``directory`` from config.ini, then
    the application default.  The iOS sandbox handling intentionally matches
    the historical ``lyrics`` behavior: relative paths are based on
    ``QOBUZ_DL_IOS_HOME`` when available, and paths outside the app's
    Documents directory are mapped by basename into that Documents directory.

    The function does not create or validate the directory; callers decide
    whether a missing directory should be created or reported.
    """
    raw = explicit_directory or configured_directory or "Qobuz Downloads"
    target = os.path.expanduser(os.fspath(raw).strip())
    if not target:
        target = "Qobuz Downloads"

    if windows is None:
        windows = os.name == "nt"
    if home is None:
        home = os.environ.get("HOME", "")
    if ios_home is None:
        ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")

    # Preserve absolute paths exactly. In particular, explicit CLI paths and
    # externally mounted iOS folders (Files/iCloud) must not be remapped.
    was_relative = not os.path.isabs(target)
    if was_relative and ios_home:
        target = os.path.join(ios_home, target)

    if was_relative and "Containers/Data/Application" in home:
        # Compatibility with the historical lyrics behavior: relative paths
        # outside the app's Documents directory are mapped by basename.
        docs_dir = os.path.join(home, "Documents")
        try:
            already_in_documents = os.path.commonpath(
                (os.path.abspath(target), os.path.abspath(docs_dir))
            ) == os.path.abspath(docs_dir)
        except ValueError:
            already_in_documents = False
        if not already_in_documents:
            base_name = os.path.basename(target.rstrip("\\/"))
            target = os.path.join(docs_dir, base_name or "Qobuz Downloads")
    elif was_relative and not ios_home:
        target = os.path.abspath(target)

    if windows:
        target = os.path.abspath(target)
        if not target.startswith("\\\\?\\"):
            target = "\\\\?\\" + target

    return target


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
      com uma mensagem pronta pra mostrar ao usuário -- quem chama só
      precisa capturar isso e exibir `str(erro)`.

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
