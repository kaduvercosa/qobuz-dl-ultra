# PARA TESTAR USE: python3 -m tests.test_lyrics
import asyncio
import configparser
import os
import time
import re

from qobuz_dl.qopy import Client
from qobuz_dl.utils import get_url_info

# =====================================================================
# FUNÇÕES DE BUSCA (Extraídas do Source 11)
# =====================================================================


def _find_url_field(data):
    """Procura, no JSON de 1o nivel, qualquer chave que pareca conter uma URL."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("url"), str):
        return data["url"]
    for k, v in data.items():
        if "url" in k.lower() and isinstance(v, str):
            return v
    return None


# =====================================================================
# FUNÇÕES DE CONVERSÃO LRC E MESCLAGEM BILINGUE
# =====================================================================


def _ms_to_lrc_timestamp(ms):
    minutes = ms // 60000
    seconds = (ms % 60000) / 1000.0
    return f"[{minutes:02d}:{seconds:06.3f}]"


def _qobuz_lines_to_lrc(lines):
    lrc_rows = []
    intro_added = False

    for entry in lines:
        start = entry.get("start")
        if start is None:
            continue

        text = (entry.get("line") or "").strip()

        if not intro_added:
            if start > 0:
                lrc_rows.append("[00:00.000]  ~ ~ ~ ")

            if text:
                lrc_rows.append(f"{_ms_to_lrc_timestamp(start)} {text}")
            else:
                if start == 0:
                    lrc_rows.append("[00:00.000]  ~ ~ ~ ")
            intro_added = True
        else:
            lrc_rows.append(f"{_ms_to_lrc_timestamp(start)} {text}")

    return "\n".join(lrc_rows) if lrc_rows else None


def _build_bilingual_lrc(original_lrc, translated_lrc):
    if not original_lrc or not translated_lrc:
        return original_lrc or translated_lrc

    def parse_lrc(lrc_text, is_translation):
        parsed = []
        for line in lrc_text.splitlines():
            tags = re.findall(r"\[\d{2,}:\d{2}\.\d{2,3}\]", line)
            text = re.sub(r"\[\d{2,}:\d{2}\.\d{2,3}\]", "", line).strip()
            if not text:
                continue
            for tag in tags:
                try:
                    m, s = tag.strip("[]").split(":")
                    s, ms = s.split(".")
                    time_ms = int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0")[:3])
                    parsed.append((time_ms, tag, text, is_translation))
                except ValueError:
                    continue
        return parsed

    orig_parsed = parse_lrc(original_lrc, False)
    trans_parsed = parse_lrc(translated_lrc, True)

    combined = orig_parsed + trans_parsed
    combined.sort(key=lambda x: (x[0], x[3]))

    final_lrc = []
    for item in combined:
        tag, text, is_trans = item[1], item[2], item[3]
        if is_trans:
            final_lrc.append(f"{tag}  ~| {text}")
        else:
            final_lrc.append(f"{tag} {text}")

    return "\n".join(final_lrc)


# =====================================================================
# FLUXO PRINCIPAL DO TESTE
# =====================================================================


async def main():
    print("\n--- 🎧 LABORATÓRIO INTERATIVO DE LETRAS (QOBUZ) ---")
    url = input("Cole o link da faixa do Qobuz (Track): ").strip()
    if not url:
        return

    url = url.replace("open.qobuz.com", "play.qobuz.com")

    try:
        url_type, item_id = get_url_info(url)
    except Exception:
        print("⚠️ Erro: Link inválido.")
        return

    home_dir = os.environ.get("HOME", "")
    config_path = os.path.join(home_dir, "Documents", "qobuz-dl", "config.ini")
    if not os.path.exists(config_path):
        config_path = os.path.join(home_dir, ".config", "qobuz-dl", "config.ini")

    config = configparser.ConfigParser()
    config.read(config_path)

    email = config.get("qobuz", "email", fallback="")
    pwd = config.get("qobuz", "password", fallback="")
    app_id = config.get("qobuz", "app_id", fallback="")
    secrets = config.get("qobuz", "secrets", fallback="").split(",")
    token = config.get("qobuz", "auth_token", fallback="")

    client = await Client.create(email, pwd, app_id, secrets, user_auth_token=token)

    try:
        print(f"\n[1/3] Obtendo informações originais da faixa...")

        # Requisição ORIGINAL com assinatura manual forte
        params_orig = {"track_id": item_id}
        params_orig["request_ts"] = int(time.time())
        params_orig["request_sig"] = client._modern_sig(
            "track/lyricsUrl", params_orig, client.sec
        )

        r_meta_orig = await client.session.request(
            "get", client.base + "track/lyricsUrl", params=params_orig
        )
        meta_orig = r_meta_orig.json()

        orig_url = _find_url_field(meta_orig)
        if not orig_url:
            print("\n⚠️ A API não retornou o link de letras originais para esta faixa.")
            return

        r_orig = await client.session.request("get", orig_url)
        content_orig = r_orig.json()

        original_lines = content_orig.get("original", {}).get("lines", [])
        lrc_original = _qobuz_lines_to_lrc(original_lines)

        available_langs = content_orig.get("translation_langs", [])
        lrc_translation = None

        if not available_langs:
            print("\n[!] Nenhuma tradução disponível no servidor para esta música.")
            print("Seguindo apenas com a letra original.")
        else:
            print(
                f"\n🌍 Traduções disponíveis encontradas: {', '.join(available_langs)}"
            )
            escolha = (
                input("Digite a sigla (ex: pt, es, en) ou 'N' para nenhuma: ")
                .strip()
                .lower()
            )

            if escolha and escolha != "n":
                print(
                    f"\n[2/3] Baixando pacote de tradução para '{escolha.upper()}'..."
                )

                # Requisição TRADUÇÃO com assinatura manual forte (A Chave do Sucesso)
                params_trans = {"track_id": item_id, "language": escolha}
                params_trans["request_ts"] = int(time.time())
                params_trans["request_sig"] = client._modern_sig(
                    "track/lyricsUrl", params_trans, client.sec
                )

                r_meta_trans = await client.session.request(
                    "get", client.base + "track/lyricsUrl", params=params_trans
                )
                meta_trans = r_meta_trans.json()

                trans_url = _find_url_field(meta_trans)

                if trans_url:
                    r_trans = await client.session.request("get", trans_url)
                    content_trans = r_trans.json()
                    trans_lines = content_trans.get("translation", {}).get("lines", [])

                    if trans_lines:
                        lrc_translation = _qobuz_lines_to_lrc(trans_lines)
                        print("✅ Tradução obtida com sucesso!")
                    else:
                        print(
                            "⚠️ O servidor retornou a requisição, mas o bloco 'translation' veio vazio."
                        )
                else:
                    print("⚠️ A API não retornou URL para essa tradução específica.")

        print("\n" + "=" * 60)
        print("🎵 RESULTADO: ARQUIVO LRC FINAL")
        print("=" * 60)

        if lrc_original:
            final_lrc = _build_bilingual_lrc(lrc_original, lrc_translation)
            print(final_lrc)
        else:
            print("(Letra original vazia ou com falha)")

    except Exception as e:
        print(f"\n❌ Erro durante a execução: {e}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
