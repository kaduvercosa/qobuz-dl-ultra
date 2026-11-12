"""Assisted Qobuz OAuth login.

This module implements the desktop-app OAuth flow used by Qobuz.  It is kept
separate from ``bundle.py`` because OAuth credentials belong to a different
Qobuz application than the Web Player credentials used by the legacy client.

The application identifiers below are public client configuration, not user
credentials.  The user's token is obtained only after the user completes the
Qobuz login/consent page in their own browser.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

# Qobuz desktop OAuth application.  Keep this pair together: do not combine
# this APP_ID with the Web Player secrets extracted by bundle.py.
OAUTH_APP_ID = "304027809"
_OAUTH_PRIVATE_KEY = "6lz8C03UDIC7"
OAUTH_APP_SECRET = "96c4538ca81015a5be0c1d5bd9573844"
API_BASE_URL = "https://www.qobuz.com/api.json/0.2"
DEFAULT_REDIRECT_PORT = 11111


class OAuthSkipped(Exception):
    """O usuário escolheu pular o OAuth e usar o login manual."""


class OAuthURLError(ValueError):
    """A URL colada pelo usuário não serve.

    A mensagem é nossa (nunca contém o texto colado, token ou código), então o
    ``cli`` pode mostrá-la ao usuário. É ``ValueError`` para não quebrar quem já
    captura ``ValueError``.
    """


def build_oauth_url(port: int = DEFAULT_REDIRECT_PORT) -> str:
    """Build the official Qobuz authorization URL for the local callback."""
    redirect_url = f"http://localhost:{int(port)}/callback"
    params = urlencode({"ext_app_id": OAUTH_APP_ID, "redirect_url": redirect_url})
    return f"https://www.qobuz.com/signin/oauth?{params}"


def extract_code_from_url(url: object) -> str:
    """Extract Qobuz's ``code_autorisation`` from a redirect URL.

    ``url`` é ``object`` de propósito: o valor vem de texto colado pelo usuário
    e a checagem ``isinstance`` abaixo é de verdade (None ou número viram
    ValueError). Com ``url: str`` o mypy a via como impossível.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("A URL de retorno do Qobuz está vazia.")
    # Colar no celular pode trazer aspas ou <> em volta: sem limpar, o sinal
    # final viraria parte do código e a troca falharia com um erro confuso.
    parsed = urlparse(url.strip().strip("'\"<>").strip())
    values = parse_qs(parsed.query).get("code_autorisation", [])
    if not values or not values[0].strip():
        if "/signin" in parsed.path or "ext_app_id" in parsed.query:
            raise OAuthURLError(
                "Esta é a URL da página do Qobuz (login ou confirmação), não a de "
                "retorno. A de retorno começa com "
                "http://localhost:11111/callback?code_autorisation= . Se o Qobuz "
                "só mostrar 'Conexão realizada' (navegador já logado), abra o "
                "link numa aba anônima ou use auth --oauth (sem --oauth-no-browser)."
            )
        raise OAuthURLError(
            "A URL não contém o parâmetro code_autorisation. "
            "Copie a URL completa exibida após autorizar o Qobuz."
        )
    return values[0].strip()


async def exchange_code(code: str) -> dict[str, str]:
    """Exchange an OAuth authorization code and validate the resulting token."""
    code = (code or "").strip()
    if not code:
        raise ValueError("Código de autorização vazio.")

    timeout = httpx.Timeout(20.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            f"{API_BASE_URL}/oauth/callback",
            params={"code": code, "private_key": _OAUTH_PRIVATE_KEY},
            headers={"X-App-Id": OAUTH_APP_ID},
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("token") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()
        if not token or not user_id:
            raise RuntimeError(
                "O Qobuz não retornou um token e um identificador de usuário válidos."
            )

        validation = await client.post(
            f"{API_BASE_URL}/user/login",
            content="extra=partner",
            headers={
                "X-App-Id": OAUTH_APP_ID,
                "X-User-Auth-Token": token,
                "Content-Type": "text/plain;charset=UTF-8",
            },
        )
        validation.raise_for_status()
        profile_payload = validation.json()

    profile = profile_payload.get("user", profile_payload)
    if not isinstance(profile, dict):
        raise RuntimeError("O Qobuz retornou um perfil de usuário inválido.")

    return {
        "app_id": OAUTH_APP_ID,
        "app_secret": OAUTH_APP_SECRET,
        "user_auth_token": token,
        "user_id": user_id,
        "display_name": str(profile.get("display_name") or profile.get("login") or ""),
        "email": str(profile.get("email") or ""),
    }


class _CallbackServer(HTTPServer):
    """Small local callback server; it never logs query strings or codes."""

    def __init__(self, address: tuple[str, int]):
        self.code: str | None = None
        self.received = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                try:
                    owner.code = extract_code_from_url(f"http://localhost{self.path}")
                    status = 200
                    body = (
                        "Autenticação concluída. Você já pode fechar esta página "
                        "e retornar ao Qobuz-DL-Ultra."
                    )
                except ValueError:
                    status = 400
                    body = "Não foi possível ler o código de autorização do Qobuz."
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                owner.received.set()

            def log_message(self, format: str, *args: object) -> None:
                # The default handler logs the callback path, which contains
                # the one-time authorization code.  Deliberately suppress it.
                return

        super().__init__(address, Handler)


def _ui():
    """Módulo de UI do programa (None se o oauth.py rodar isolado)."""
    try:
        from qobuz_dl import ui
    except ImportError:
        return None
    return ui


def _cores(accent: str | None = None) -> tuple[str, str]:
    """Devolve (destaque, reset) respeitando --no-color, como o assistente."""
    ui = _ui()
    try:
        from qobuz_dl.color import HIGHLIGHT, OFF
    except ImportError:
        return "", ""
    if ui is None:
        return "", ""
    return (accent if accent is not None else ui.c(HIGHLIGHT)), ui.c(OFF)


def _titulo(texto: str, accent: str | None = None) -> str:
    """Título de seção no padrão do assistente: [ TEXTO ] em negrito e destaque."""
    ui = _ui()
    cor, off = _cores(accent)
    negrito = ""
    if ui is not None:
        try:
            from qobuz_dl.color import ANSI_BOLD

            negrito = ui.c(ANSI_BOLD)
        except ImportError:
            negrito = ""
    return f"{negrito}{cor}[ {texto.upper()} ]{off}"


def _say(kind: str, text: str = "") -> None:
    """Imprime com ui.<kind> (warn, detail...) ou print() sem a UI."""
    ui = _ui()
    fn = getattr(ui, kind, None) if ui is not None else None
    if fn is None:
        print(text)
    else:
        fn(text)


def _say_always(text: str = "") -> None:
    """Como ui.emit_always: aparece mesmo com --quiet (menus e links)."""
    ui = _ui()
    fn = None
    if ui is not None:
        fn = getattr(ui, "emit_always", None) or getattr(ui, "emit", None)
    if fn is None:
        print(text)
    else:
        fn(text)


def _step(text: str, accent: str | None = None) -> None:
    """Etapa em andamento ([*]) na cor de destaque do assistente."""
    ui = _ui()
    emit_tagged = getattr(ui, "_emit_tagged", None) if ui is not None else None
    if emit_tagged is None:
        _say("step", text)
        return
    cor, _off = _cores(accent)
    emit_tagged(cor, "*", text)


def _ask_before_opening_browser(accent: str | None = None) -> str:
    """Pergunta antes de abrir o navegador.

    Devolve ``"open"`` (abre o navegador), ``"url"`` (só mostra o link) ou
    ``"manual"`` (pula o OAuth). Sem terminal interativo devolve ``"url"``,
    para nunca abrir o navegador sozinho em scripts, cron ou apps externos.
    """
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return "url"

    _say_always("")
    _say_always(_titulo("Autorização no navegador (OAuth)", accent))
    _say_always("  Enter  abrir o navegador agora")
    _say_always("  u      só mostrar o link (para abrir em outro aparelho)")
    _say_always("  m      pular e usar o token manual")
    try:
        answer = input("Escolha (Enter para abrir): ").strip().lower()
    except EOFError:
        return "manual"
    if answer in ("m", "manual"):
        return "manual"
    if answer in ("u", "url", "link"):
        return "url"
    return "open"


async def obtain_authorization_code(
    port: int = DEFAULT_REDIRECT_PORT,
    no_browser: bool = False,
    confirm: bool = False,
    accent: str | None = None,
) -> str:
    """Open the browser callback flow, with a paste-URL fallback for headless use.

    With ``confirm=True`` the user is asked before the browser is opened and
    may choose to only see the link or to skip OAuth (``OAuthSkipped``).
    ``accent`` is the accent color escape used by the setup wizard.
    """
    url = build_oauth_url(port)
    cor, off = _cores(accent)
    if no_browser:
        _say_always("")
        _say_always(
            f"{cor}! Abra este endereço em um navegador e autorize o Qobuz:{off}"
        )
        _say_always(f"  {url}")
        redirect_url = input("Cole aqui a URL completa de retorno: ").strip()
        return extract_code_from_url(redirect_url)

    # Avoid launching xdg-open on headless Linux hosts (common on EC2): it can
    # report success while no browser is actually available to receive the URL.
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return await obtain_authorization_code(port, no_browser=True, accent=accent)

    if confirm:
        choice = _ask_before_opening_browser(accent)
        if choice == "manual":
            raise OAuthSkipped()
        if choice == "url":
            return await obtain_authorization_code(port, no_browser=True, accent=accent)

    try:
        server = _CallbackServer(("localhost", int(port)))
    except OSError as exc:
        logger.info(
            "Callback local indisponível (%s); usando retorno manual.",
            type(exc).__name__,
        )
        return await obtain_authorization_code(port, no_browser=True, accent=accent)

    server.timeout = 0.5
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.2},
        daemon=True,
    )
    thread.start()
    try:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            _say("warn", "Não foi possível abrir o navegador automaticamente.")
            _say_always(f"{cor}! Abra este endereço manualmente:{off}")
            _say_always(f"  {url}")
            redirect_url = input("Cole aqui a URL completa de retorno: ").strip()
            return extract_code_from_url(redirect_url)

        _step(
            f"Aguardando autorização do Qobuz em localhost:{port} (até 5 minutos)...",
            accent,
        )
        if not server.received.wait(300.0):
            raise TimeoutError(
                "Tempo limite aguardando o retorno do Qobuz. "
                "Tente novamente com --oauth-no-browser."
            )
        if not server.code:
            raise ValueError(
                "O retorno recebido não continha um código válido. "
                "Tente novamente com --oauth-no-browser."
            )
        return server.code
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


async def login(
    port: int = DEFAULT_REDIRECT_PORT,
    no_browser: bool = False,
    confirm: bool = False,
    accent: str | None = None,
) -> dict[str, str]:
    """Run OAuth and return validated credentials without persisting them."""
    code = await obtain_authorization_code(
        port=port, no_browser=no_browser, confirm=confirm, accent=accent
    )
    return await exchange_code(code)
