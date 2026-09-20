#!/usr/bin/env python3
"""tools/canary_check_busca.py -- verifica se login + busca de álbum +
metadata completa ainda funcionam contra a API real da Qobuz.

POR QUE ESSE CHECK, ALÉM DO canary_check.py
-----------------------------------------------
canary_check.py cobre o ponto mais frágil (extração de app_id/secrets do
bundle.js) SEM precisar de login -- ótimo, mas não é o único jeito da API
quebrar. Depois do login, a Qobuz ainda pode mudar o FORMATO da resposta
de busca/metadata (renomear um campo, aninhar diferente) sem que a
extração do bundle tenha nada a ver com isso. Esse check aqui exercita
esse segundo trecho: login real -> busca -> confere se os campos que o
downloader.py depende (id, title, artist.name, tracks.items) continuam
no lugar esperado.


Precisa de um user_auth_token real (QOBUZ_EMAIL/QOBUZ_TOKEN como variável
de ambiente -- o mesmo token que você cola na hora de configurar o app
normalmente, não uma senha; o fluxo real de login deste projeto usa
email+user_auth_token, com pwd="" fixo -- ver cli.py). Recomendo uma
conta separada só pra isso, não a sua principal.

Um detalhe pra não confundir alerta: se esse token expirar/for
revogado, este check falha por ISSO, não por mudança na API da Qobuz --
o problema vai aparecer como "login falhou" na mensagem, então já dá
pra distinguir sem precisar investigar mais.

Uso: QOBUZ_EMAIL=... QOBUZ_TOKEN=... python tools/canary_check_busca.py
Saída: código 0 se tudo certo, código 1 (com mensagem clara) se algo
quebrou -- mesmo padrão do canary_check.py, pensado pra virar falha de CI.
"""

import asyncio
import os
import sys

from qobuz_dl.qopy import Client


async def checar() -> list[str]:
    """Devolve uma lista de problemas encontrados (vazia = tudo ok)."""
    problemas = []

    email = os.environ.get("QOBUZ_EMAIL")
    token = os.environ.get("QOBUZ_TOKEN")
    if not email or not token:
        return [
            "QOBUZ_EMAIL/QOBUZ_TOKEN não definidos -- configure os "
            "secrets no repositório (ver .github/workflows/canary.yaml)."
        ]

    try:
        client = await Client.create(
            email=email, pwd="", app_id="", secrets=[], user_auth_token=token
        )
    except Exception as e:
        return [
            f"Login falhou -- token inválido/expirado ou fluxo de auth quebrado: {type(e).__name__}: {e}"
        ]

    try:
        try:
            resultado = await client.search_albums("The Beatles", limit=5)
        except Exception as e:
            problemas.append(f"search_albums() lançou {type(e).__name__}: {e}")
            return problemas

        albuns = (resultado or {}).get("albums", {}).get("items", [])
        if not albuns:
            problemas.append(
                "Busca de álbuns voltou vazia ou em formato inesperado -- "
                f"resposta bruta: {resultado!r}"
            )
            return problemas

        primeiro = albuns[0]
        album_id = primeiro.get("id")
        if not album_id:
            problemas.append(
                f"Item de álbum sem campo 'id' -- item bruto: {primeiro!r}"
            )
            return problemas

        try:
            meta = await client.get_album_meta(album_id)
        except Exception as e:
            problemas.append(f"get_album_meta() lançou {type(e).__name__}: {e}")
            return problemas

        campos_essenciais = {
            "id": meta.get("id"),
            "title": meta.get("title"),
            "artist.name": (meta.get("artist") or {}).get("name"),
            "tracks.items": (meta.get("tracks") or {}).get("items"),
        }
        faltando = [nome for nome, valor in campos_essenciais.items() if not valor]
        if faltando:
            problemas.append(
                f"Metadata do álbum veio sem os campos {faltando} -- a Qobuz "
                "pode ter mudado o formato da resposta. Chaves de nível "
                f"superior recebidas: {sorted(meta.keys())}"
            )
    finally:
        await client.close()

    return problemas


def main():
    problemas = asyncio.run(checar())
    if not problemas:
        print("OK -- login, busca e metadata de álbum funcionando normalmente.")
        return 0

    print("FALHOU -- possível mudança na API da Qobuz:", file=sys.stderr)
    for p in problemas:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
