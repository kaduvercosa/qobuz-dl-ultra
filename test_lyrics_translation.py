import asyncio
import configparser
import os
import json
import time

from qobuz_dl.qopy import Client
from qobuz_dl.utils import get_url_info


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


async def _fetch_json_url(client, url):
    async with client.session.request("get", url) as r:
        status = r.status
        try:
            data = await r.json()
        except Exception:
            data = {"_raw_text": (await r.text())[:500]}
        return status, data


def _print_block(label, block):
    """
    Imprime um bloco no formato {'lang':..., 'lines':[{'line','start','end'}]},
    igual ao que 'original' usa -- pra comparar visualmente se 'translation'
    segue exatamente o mesmo esquema.
    """
    print(f"\n--- {label} ---")
    if not isinstance(block, dict):
        print(f"  (nao e' um dict -- valor bruto: {json.dumps(block, ensure_ascii=False)[:500]})")
        return

    print(f"  type: {block.get('type', '?')}")
    print(f"  lang: {block.get('lang', '?')}")
    lines = block.get("lines")
    if not isinstance(lines, list):
        print(f"  lines: (ausente ou formato inesperado -- chaves do bloco: {list(block.keys())})")
        return

    print(f"  lines: {len(lines)} entradas")
    print("  primeiras 8 linhas (com timing, mesmo esquema de 'original'):")
    shown = 0
    for entry in lines:
        if shown >= 8:
            break
        text = (entry.get("line") or "").strip()
        start = entry.get("start")
        end = entry.get("end")
        if start is None and not text:
            continue  # separador estrutural, igual ao que o parser real ja ignora
        print(f"    start={start} end={end} line={text!r}")
        shown += 1


async def main():
    print("\n--- TESTE FOCADO: track/lyricsUrl com language=pt ---")
    url = input("Cole o link da faixa do Qobuz (Track): ").strip()
    if not url:
        return

    url = url.replace("open.qobuz.com", "play.qobuz.com")

    try:
        url_type, item_id = get_url_info(url)
        if url_type != "track":
            print("⚠️ Erro: Insira o link de uma FAIXA (track), nao de um album inteiro.")
            return
    except Exception:
        print("⚠️ Erro: Link invalido.")
        return

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
        print("⚠️ Erro: config.ini nao encontrado.")
        return

    print(f"\n[1/2] Autenticando na API (Track ID: {item_id})...")
    client = await Client.create(email, pwd, app_id, secrets, user_auth_token=token)

    try:
        print("[2/2] Chamando track/lyricsUrl com language=pt...")

        params = {"track_id": item_id, "language": "pt"}
        params["request_ts"] = int(time.time())
        params["request_sig"] = client._modern_sig("track/lyricsUrl", params, client.sec)

        async with client.session.request(
            "get", client.base + "track/lyricsUrl", params=params
        ) as r:
            status = r.status
            meta = await r.json()

        print(f"\nStatus HTTP da chamada com language=pt: {status}")
        print("\n=== JSON BRUTO DE NIVEL 1 (metadata + link) ===")
        print(json.dumps(meta, indent=2, ensure_ascii=False))

        lyrics_json_url = _find_url_field(meta)
        if not lyrics_json_url:
            print("\n⚠️ Nao achei nenhuma URL na resposta. Nada mais a fazer.")
            return

        content_status, content = await _fetch_json_url(client, lyrics_json_url)
        if content_status != 200:
            print(f"\n⚠️ A URL de conteudo nao retornou 200 (status={content_status}).")
            return

        print("\n=== JSON BRUTO DE NIVEL 2 (conteudo apontado pela URL) ===")
        print(json.dumps(content, indent=2, ensure_ascii=False))

        # Agora a parte que interessa: comparar 'original' e 'translation'
        # lado a lado, no mesmo formato, pra confirmar se 'translation'
        # segue o mesmo esquema (type/lang/lines) que 'original'.
        original = content.get("original") if isinstance(content, dict) else None
        translation = content.get("translation") if isinstance(content, dict) else None

        print("\n" + "=" * 60)
        print("COMPARACAO ORIGINAL vs TRANSLATION")
        print("=" * 60)

        if original is not None:
            _print_block("original", original)
        else:
            print("\n--- original ---\n  (chave ausente nessa resposta)")

        if translation is not None:
            _print_block("translation", translation)
            if isinstance(translation, dict) and isinstance(translation.get("lines"), list):
                print(
                    "\n✅ 'translation' tem o mesmo formato de 'original' "
                    "(type/lang/lines com start/end) -- da' pra reaproveitar "
                    "_qobuz_lines_to_lrc()/_qobuz_lines_to_plain() direto nela."
                )
        else:
            print("\n--- translation ---\n  (chave ausente nessa resposta -- nao rolou dessa vez)")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())