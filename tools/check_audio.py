import configparser
import os
import subprocess
import sys
from typing import Literal

from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style

try:
    from qobuz_dl.utils import verify_audio_integrity
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from qobuz_dl.utils import verify_audio_integrity

pt_style = Style.from_dict(
    {
        "title": "ansicyan bold",
        "pointer": "ansiyellow bold",
        "hovered": "bg:#cccccc fg:#000000 bold",
    }
)

Verificacao = tuple[Literal[True], str] | tuple[Literal[False], str] | tuple[None, str]


def get_root_from_config() -> str | None:
    """Search for a music directory path in nearby config files."""
    for cfg_name in ["config.ini", "settings.ini"]:
        if os.path.exists(cfg_name):
            config = configparser.ConfigParser()
            config.read(cfg_name)
            for section in config.sections():
                for key in ["directory", "download_path", "dir"]:
                    if key in config[section]:
                        path = config[section][key]
                        if os.path.isdir(path):
                            return path
    return None


def find_audio_files(root_dir: str) -> list[tuple[str, str]]:
    """Recursively find all audio files in root_dir.

    Returns a list of ``(filename, full_path)`` tuples.
    """
    audio_extensions = (".flac", ".mp3", ".m4a", ".wav", ".alac")
    audio_files: list[tuple[str, str]] = []

    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(audio_extensions):
                full_path = os.path.join(dirpath, filename)
                audio_files.append((filename, full_path))

    return audio_files


def verify_library(root_dir: str) -> int:
    """Varre a biblioteca e verifica a integridade de cada arquivo de áudio."""
    print(f"\n🔍 Varrendo '{root_dir}' e verificando integridade de cada arquivo...\n")
    audio_list = find_audio_files(root_dir)

    if not audio_list:
        print("\033[33m[!] Nenhum arquivo de áudio encontrado.\033[0m")
        return 0

    total = len(audio_list)
    corrupted: list[tuple[str, str]] = []
    unverified: list[tuple[str, str]] = []

    for idx, (filename, full_path) in enumerate(audio_list, start=1):
        sys.stdout.write(f"\r  [{idx}/{total}] Verificando: {filename[:50]:<50}")
        sys.stdout.flush()

        ok, message = verify_audio_integrity(full_path)

        if ok is False:
            corrupted.append((full_path, message))
        elif ok is None:
            unverified.append((full_path, message))

    sys.stdout.write("\r" + " " * 90 + "\r")

    verified_ok = total - len(corrupted) - len(unverified)
    print(f"✅ {verified_ok}/{total} arquivos validados sem erros.")

    if unverified:
        print(f"⚠️ {len(unverified)} arquivo(s) não puderam ser verificados.\n")

    if corrupted:
        print(f"\033[31m[!] {len(corrupted)} arquivo(s) com problema:\033[0m")
        for full_path, message in corrupted:
            print(f"  \033[31m✗\033[0m {full_path}")
            print(f"      {message}")
        return 1

    if unverified:
        print("[!] Instale ffmpeg para verificar todos os arquivos.")
        for full_path, message in unverified:
            print(f"  ? {full_path}")
            print(f"      {message}")
        return 2

    print("\033[32m✓ Biblioteca íntegra, nenhum problema encontrado.\033[0m")
    return 0


def main() -> None:
    """Interactive audio file inspector using ffprobe."""
    print("\033[36m=== Inspecionador Completo de Áudio (ffprobe) ===\033[0m\n")

    target_dir = ""
    config_dir = get_root_from_config()

    if config_dir:
        print(f"📁 Pasta encontrada no config.ini: \033[33m{config_dir}\033[0m")
        resp = input("Deseja usá-la? [S/n]: ").strip().lower()
        if resp in ("", "s", "sim"):
            target_dir = config_dir

    if not target_dir:
        default_path = os.getcwd()
        print(f"📂 Diretório atual de trabalho: \033[33m{default_path}\033[0m")
        user_input = (
            input(
                "Digite o nome da pasta de músicas "
                "(ou aperte Enter para usar a atual): "
            )
            .strip()
            .strip('"')
            .strip("'")
        )

        if not user_input:
            target_dir = default_path
        elif os.path.isabs(user_input) and os.path.isdir(user_input):
            target_dir = user_input
        else:
            possible_path = os.path.join(default_path, user_input)
            if os.path.isdir(possible_path):
                target_dir = possible_path
            else:
                docs_path = os.path.expanduser(f"~/Documents/{user_input}")
                if os.path.isdir(docs_path):
                    target_dir = docs_path
                else:
                    target_dir = user_input

    if not target_dir or not os.path.isdir(target_dir):
        print(
            f"\n\033[31m[!] O diretório informado não existe ou não pôde ser "
            f"acessado: '{target_dir}'\033[0m"
        )
        sys.exit(1)

    print(f"\n🔍 Varrendo subpastas em '{target_dir}'...")
    audio_list = find_audio_files(target_dir)

    if not audio_list:
        print("\n[!] Nenhum arquivo de áudio encontrado nas subpastas")
        sys.exit(0)

    print(f"✨ Encontrados {len(audio_list)} arquivos de áudio.\n")

    options: list[tuple[int, str]] = []
    for idx, (filename, full_path) in enumerate(audio_list):
        rel_path = os.path.relpath(full_path, target_dir)
        options.append((idx, f"{filename} ({os.path.dirname(rel_path)})"))

    try:
        selected_idx = radiolist_dialog(
            title="Selecione a música para inspecionar",
            text="Use as setas para mover e ENTER para escolher:",
            values=options,
            style=pt_style,
        ).run()

        if selected_idx is None:
            print("\nOperação cancelada.")
            sys.exit(0)

        chosen_file = audio_list[selected_idx][1]

        print(f"\n\033[32m▶ Dados técnicos completos para:\033[0m {chosen_file}\n")
        print("=" * 70)

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-hide_banner",
            "-print_format",
            "flat",
            "-show_format",
            "-show_streams",
            chosen_file,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("\n[!] Erro ao executar o ffprobe.")
            print(result.stderr)
        else:
            for line in result.stdout.splitlines():
                print(f"  {line}")
            print("=" * 70)

    except KeyboardInterrupt:
        print("\nSaindo...")


if __name__ == "__main__":
    if "--verify-library" in sys.argv:
        args = [argument for argument in sys.argv[1:] if argument != "--verify-library"]
        target = args[0] if args else (get_root_from_config() or os.getcwd())
        sys.exit(verify_library(target))
    else:
        main()
