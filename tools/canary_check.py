#!/usr/bin/env python3
"""tools/canary_check.py -- verifica se a extração de app_id/secrets do
Qobuz (qobuz_dl/bundle.py) ainda funciona, sem precisar de login.

POR QUE ESSE CHECK EM ESPECÍFICO
-----------------------------------
De tudo que pode "quebrar por mudança na API", a extração do app_id/
secrets a partir do bundle.js público do site do Qobuz é o ponto mais
frágil e mais silencioso: o Qobuz não avisa quando muda o formato do
HTML de login ou do bundle.js, e quando muda, os regex de bundle.py
param de casar -- às vezes lançando uma exceção clara
(NotImplementedError), às vezes só devolvendo um dict vazio ou um valor
de secret corrompido (erro de padding no base64), dependendo de QUAL
parte mudou. Rodar isso periodicamente pega a quebra no mesmo dia, em
vez de só quando um download de verdade falhar.

NÃO faz login, não baixa nenhuma música, não precisa de nenhuma
credencial -- só a página pública /login e o bundle.js público, os
mesmos dois arquivos que o navegador de qualquer visitante carrega.

Uso: python tools/canary_check.py
Saída: código 0 se tudo certo, código 1 (com mensagem clara) se algo
quebrou -- pensado pra virar uma falha de CI que dispara notificação.
"""
import sys

from qobuz_dl.bundle import Bundle


def checar() -> list[str]:
    """Devolve uma lista de problemas encontrados (vazia = tudo ok)."""
    problemas = []

    try:
        bundle = Bundle()
    except Exception as e:
        # Cobre o NotImplementedError documentado em bundle.py (URL do
        # bundle não encontrada no HTML de login) e qualquer erro de
        # rede/HTTP (raise_for_status).
        return [f"Não consegui nem baixar o bundle.js: {type(e).__name__}: {e}"]

    try:
        app_id = bundle.get_app_id()
        if not app_id or not app_id.isdigit():
            problemas.append(
                f"app_id veio vazio ou não-numérico: {app_id!r} -- "
                "o formato do objeto 'production:{api:{appId:...}}' no "
                "bundle provavelmente mudou."
            )
    except Exception as e:
        problemas.append(f"get_app_id() lançou {type(e).__name__}: {e}")

    try:
        secrets = bundle.get_secrets()
        if not secrets:
            problemas.append(
                "get_secrets() devolveu vazio -- os regex de seed/timezone "
                "não casaram com nada no bundle atual."
            )
        else:
            for timezone, secret in secrets.items():
                if not secret or not isinstance(secret, str):
                    problemas.append(
                        f"secret da timezone '{timezone}' veio vazio ou "
                        f"inválido: {secret!r} -- possível mudança no "
                        "tamanho do padding de ofuscação (hoje 44 chars) "
                        "ou no formato base64."
                    )
    except Exception as e:
        problemas.append(f"get_secrets() lançou {type(e).__name__}: {e}")

    return problemas


def main():
    problemas = checar()
    if not problemas:
        print("OK -- app_id e secrets extraídos normalmente do bundle atual.")
        return 0

    print("FALHOU -- possível mudança na API/site do Qobuz:", file=sys.stderr)
    for p in problemas:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
