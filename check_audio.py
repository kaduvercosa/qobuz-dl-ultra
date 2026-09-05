import configparser
import os
import subprocess
import sys

try:
    from prompt_toolkit.shortcuts import radiolist_dialog
    from prompt_toolkit.styles import Style
except ImportError:
    sys.exit(
        "Erro: Por favor, instale o prompt_toolkit executando: pip install prompt_toolkit"
    )

# Reusa a mesma logica de verificacao que o downloader.py agora chama
# automaticamente apos cada download (qobuz_dl/utils.py). Antes, este
# script so mostrava metadados via ffprobe (-show_format/-show_streams) --
# nao decodificava o arquivo, entao nao pegava corrupcao real no meio do
# audio. Import feito com fallback de path pra funcionar tanto rodando
# "python check_audio.py" direto da raiz do repo quanto com o pacote
# instalado via "pip install -e .".
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


def get_root_from_config():
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


def find_audio_files(root_dir):
    audio_extensions = (".flac", ".mp3", ".m4a", ".wav", ".alac")
    audio_files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for f in filenames:
            if f.lower().endswith(audio_extensions):
                full_path = os.path.join(dirpath, f)
                audio_files.append((f, full_path))
    return audio_files


def verify_library(root_dir):
    """
    Varre root_dir inteiro e roda verify_audio_integrity() em cada arquivo
    de audio encontrado, imprimindo um relatorio final com o que esta
    corrompido. E o modo nao-interativo, pensado pra rodar depois de um
    download em lote grande (discografia, playlist) ou periodicamente
    sobre a biblioteca inteira -- diferente do modo interativo abaixo,
    que so inspeciona um arquivo por vez, escolhido na mao.
    """
    print(f"\n🔍 Varrendo '{root_dir}' e verificando integridade de cada arquivo...\n")
    audio_list = find_audio_files(root_dir)

    if not audio_list:
        print("\033[33m[!] Nenhum arquivo de áudio encontrado.\033[0m")
        return 0

    total = len(audio_list)
    corrupted = []

    for idx, (filename, full_path) in enumerate(audio_list, start=1):
        sys.stdout.write(f"\r  [{idx}/{total}] Verificando: {filename[:50]:<50}")
        sys.stdout.flush()

        ok, message = verify_audio_integrity(full_path)
        if not ok:
            corrupted.append((full_path, message))

    sys.stdout.write("\r" + " " * 90 + "\r")

    print(f"✅ {total - len(corrupted)}/{total} arquivos OK.\n")

    if corrupted:
        print(f"\033[31m[!] {len(corrupted)} arquivo(s) com problema:\033[0m")
        for full_path, message in corrupted:
            print(f"  \033[31m✗\033[0m {full_path}")
            print(f"      {message}")
        return 1

    print("\033[32m✓ Biblioteca íntegra, nenhum problema encontrado.\033[0m")
    return 0


def main():
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
                "Digite o nome da pasta de músicas (ou aperte Enter para usar a atual): "
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
            f"\n\033[31m[!] O diretório informado não existe ou não pôde ser acessado: '{target_dir}'\033[0m"
        )
        sys.exit(1)

    print(f"\n🔍 Varrendo subpastas em '{target_dir}'...")
    audio_list = find_audio_files(target_dir)

    if not audio_list:
        print(f"\n[!] Nenhum arquivo de áudio encontrado nas subpastas")
        sys.exit(0)

    print(f"✨ Encontrados {len(audio_list)} arquivos de áudio.\n")

    options = []
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

        # Removemos os filtros para exibir o relatório técnico completo do ffprobe
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
            print(f"\n[!] Erro ao executar o ffprobe.")
            print(result.stderr)
        else:
            for line in result.stdout.splitlines():
                print(f"  {line}")
            print("=" * 70)

    except KeyboardInterrupt:
        print("\nSaindo...")


if __name__ == "__main__":
    # --verify-library [pasta] roda a varredura em lote (nao-interativa) em
    # vez do inspetor de arquivo unico. Sem argumento de pasta, usa a mesma
    # deteccao via config.ini que o modo interativo ja usava, ou o diretorio
    # atual como ultimo recurso.
    if "--verify-library" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--verify-library"]
        target = args[0] if args else (get_root_from_config() or os.getcwd())
        sys.exit(verify_library(target))
    else:
        main()
