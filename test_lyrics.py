import asyncio
import configparser
import os
import json

from qobuz_dl.qopy import Client
from qobuz_dl.utils import get_url_info


async def main():
    print("\n--- TESTADOR DE ENDPOINT track/lyricsUrl (QOBUZ) ---")
    url = input("Cole o link da faixa do Qobuz (Track): ").strip()
    if not url:
        return

    # Arruma o domínio caso venha do app novo
    url = url.replace("open.qobuz.com", "play.qobuz.com")

    try:
        url_type, item_id = get_url_info(url)
        if url_type != "track":
            print("⚠️ Erro: Insira o link de uma FAIXA (track), não de um álbum inteiro.")
            return
    except Exception:
        print("⚠️ Erro: Link inválido.")
        return

    # Busca o config.ini no diretório correto do iOS
    home_dir = os.environ.get("HOME", "")
    config_path = os.path.join(home_dir, "Documents", "qobuz-dl", "config.ini")
    if not os.path.exists(config_path):
        config_path = os.path.join(home_dir, ".config", "qobuz-dl", "config.ini")

    config = configparser.ConfigParser()
    config.read(config_path)

    try:
        email = config.get("qobuz", "email", fallback="")
        pwd = config.get("qobuz", "password", fallback="")
        app_id = config.get("qobuz", "app_id", fallback="")
        secrets = config.get("qobuz", "secrets", fallback="").split(",")
        token = config.get("qobuz", "auth_token", fallback="")
    except configparser.NoSectionError:
        print("⚠️ Erro: config.ini não encontrado.")
        return

    print(f"\n[1/2] Autenticando na API (Track ID: {item_id})...")
    client = await Client.create(email, pwd, app_id, secrets, user_auth_token=token)

    try:
        print("[2/2] Chamando o endpoint 'track/lyricsUrl' (agora assinado automaticamente)...")
        # Com o patch no qopy.py, api_call já monta request_ts + request_sig
        # sozinho para esse endpoint. Não precisamos fazer nada manual aqui.
        lyrics_url_meta = await client.api_call("track/lyricsUrl", track_id=item_id)

        print("\n" + "=" * 60)
        print("🕵️  INSPEÇÃO BRUTA DO JSON RETORNADO PELO ENDPOINT")
        print("=" * 60)
        print(json.dumps(lyrics_url_meta, indent=2, ensure_ascii=False))
        print("=" * 60)

        # Procura qualquer chave que pareça conter a URL da letra
        suspect_keys = [
            k for k in lyrics_url_meta.keys()
            if "lyric" in k.lower() or "url" in k.lower()
        ]
        print(
            f"\nChaves suspeitas encontradas: {
                suspect_keys if suspect_keys else 'Nenhuma'}")

    except Exception as e:
        # Se o secret estiver errado ou o endpoint mudar, o erro aparece aqui
        print(f"\n❌ Erro ao chamar o endpoint: {e}")

    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
