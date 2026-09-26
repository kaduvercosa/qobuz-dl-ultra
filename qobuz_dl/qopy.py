# ============================================================================
# qopy.py -- cliente assincrono da API Qobuz com autenticacao, criptografia e fallback.
# Ponto de entrada: Client.create(...). Principais rotinas: api_call(), get_track_url().
# ============================================================================
import asyncio
import base64
import hashlib
import logging
import time
import unicodedata
from datetime import date, datetime
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# Import condicional do cryptography para compatibilidade com iOS/a-Shell
try:
    from cryptography.hazmat.primitives import hashes, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    _CRYPTO_AVAILABLE = True
except ImportError:
    hashes = padding = Cipher = algorithms = modes = HKDF = None
    _CRYPTO_AVAILABLE = False

from qobuz_dl import ui
from qobuz_dl.color import GREEN
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import OFF, RED, RESET
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.exceptions import (
    AuthenticationError,
    InvalidAppSecretError,
    InvalidQuality,
    NoActiveSubscriptionError,
)

Bundle: Any = None
try:
    from qobuz_dl.bundle import Bundle as _Bundle

    Bundle = _Bundle
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _resolve_user_auth_token(client, token=None):
    """Resolve token de múltiplas fontes para evitar AttributeError em testes."""
    for value in (
        token,
        getattr(client, "uat", None),
        getattr(client, "user_auth_token", None),
    ):
        if value:
            return str(value).strip()
    return ""


class Client:
    """
    O cliente principal da API Qobuz para o Qobuz-DL Ultra Edition.
    """

    def __init__(self):
        self.session = None
        self.user_info = {}
        self.user_id = None
        self.label = "Studio"
        self.uat = None
        self.user_auth_token = None

    @classmethod
    async def create(
        cls,
        email,
        pwd,
        app_id,
        secrets,
        user_auth_token=None,
        force_english=True,
        **kwargs,
    ):
        """Fabrica assincrona."""
        self = cls()
        print(f"{YELLOW}Logando...{OFF}", end="", flush=True)
        self.secrets = secrets
        self.id = str(app_id)
        self.force_english = force_english

        if not self.id or self.id == "798273057":
            if Bundle:
                try:
                    b = await Bundle.create()
                    fresh_id = str(b.get_app_id())
                    if fresh_id:
                        self.id = fresh_id
                        self.secrets = list(b.get_secrets().values())
                        logger.info(
                            f"\r{GREEN}[+] ID atualizado dinamicamente: {self.id}{OFF}\033[K"
                        )
                except Exception:
                    ui.emit(
                        f"\r{YELLOW} [!] ID nao atualizado (usando padrao).{OFF}\033[K"
                    )
            else:
                logger.info(
                    f"\r{GREEN}[+] Usando ID legado personalizado: {self.id}{OFF}\033[K"
                )

        headers = {}
        if self.force_english:
            headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "X-App-Language": "en",
                    "X-App-Region": "US",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-site",
                    "X-App-Id": self.id,
                }
            )
        else:
            headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-App-Id": self.id,
                }
            )

        client_timeout = httpx.Timeout(None, connect=15.0, read=90.0)
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)

        self.session = httpx.AsyncClient(
            headers=headers, timeout=client_timeout, limits=limits
        )
        self.base = "https://www.qobuz.com/api.json/0.2/"
        self.sec = None
        self.session_id = None
        self._session_init_lock = asyncio.Lock()
        self.session_infos = None
        self.session_key = None
        self.uat = None
        self.user_auth_token = None

        try:
            await self.auth(email, pwd, user_auth_token)
            await self.cfg_setup()
        except BaseException:
            await self.close()
            raise
        return self

    async def close(self):
        if self.session is not None:
            await self.session.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    def _normalize_json_strings(self, obj):
        if isinstance(obj, str):
            if "..." in obj and "://" not in obj:
                obj = obj.replace("...", "…")
            return unicodedata.normalize("NFC", obj)
        elif isinstance(obj, dict):
            return {k: self._normalize_json_strings(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._normalize_json_strings(item) for item in obj]
        else:
            return obj

    def _token_candidates(self, user_auth_token=None):
        """Retorna o token efetivo, aceitando os nomes usados pelo projeto."""
        candidates = (
            user_auth_token,
            getattr(self, "uat", None),
            getattr(self, "user_auth_token", None),
        )
        return next((str(token).strip() for token in candidates if token), "")

    async def auth(self, email, pwd, user_auth_token=None):
        token = _resolve_user_auth_token(self, user_auth_token)
        if token:
            self.uat = token
            self.user_auth_token = token
            if self.session is not None:
                self.session.headers.update({"X-User-Auth-Token": token})
        elif len(pwd or "") > 60:
            self.uat = str(pwd).strip()
            self.user_auth_token = self.uat
            if self.session is not None:
                self.session.headers.update({"X-User-Auth-Token": self.uat})
        else:
            usr_info = await self.api_call("user/login", email=email, pwd=pwd)
            if not usr_info.get("user", {}).get("credential", {}).get("parameters"):
                logger.info(
                    f"{YELLOW}[!] Conta gratuita detectada ou validacao ignorada.{OFF}"
                )
            self.uat = usr_info["user_auth_token"]
            self.user_auth_token = self.uat
            if self.session is not None:
                self.session.headers.update({"X-User-Auth-Token": self.uat})

        try:
            raw_user_info = await self.api_call("user/get")
            self.user_info = raw_user_info.get("user", raw_user_info) or {}
            cred = self.user_info.get("credential") or {}
            self.label = cred.get("parameters", {}).get("short_label") or cred.get(
                "description", "Membro Qobuz"
            )
            self.user_id = self.user_info.get("id")

            sub = self.check_subscription()
            if sub["is_active"]:
                logger.info(f"{GREEN}Logado: OK (Assinatura: {self.label}){OFF}")
            else:
                logger.warning(
                    f"{YELLOW}[!] Logado: OK, mas a assinatura esta {RED}INATIVA{RESET} ({sub['status']}){OFF}"
                )
        except Exception:
            logger.info(f"{YELLOW}[!] Validacao do perfil ignorada.{OFF}")
            self.label = "Studio"
            self.user_id = None

    def _find_subscription(self, value):
        """Procura somente dados reais de assinatura no JSON, sem inferencia falsa."""
        if isinstance(value, dict):
            direct = value.get("subscription")
            if isinstance(direct, dict):
                return direct
            for key in ("user", "account", "profile", "credential"):
                found = self._find_subscription(value.get(key))
                if found is not None:
                    return found
        return None

    def check_subscription(self) -> dict[str, Any]:
        user_info = self.user_info or {}
        sub = self._find_subscription(user_info)
        if not isinstance(sub, dict):
            return {
                "is_active": False,
                "status": "Inativa / Sem Assinatura",
                "offer": "Nenhuma / Gratuita",
                "start_date": None,
                "end_date": None,
                "is_canceled": False,
                "periodicity": "N/A",
                "household_size_max": 1,
                "raw": {},
            }

        offer_raw = sub.get("offer") or "n/a"
        offer = str(offer_raw).capitalize()
        start_date = sub.get("start_date") or sub.get("start")
        end_date = sub.get("end_date") or sub.get("end")
        is_canceled = bool(sub.get("is_canceled", False))
        periodicity = sub.get("periodicity") or "N/A"
        household_size_max = sub.get("household_size_max", 1)

        is_free = offer.lower() in {"free", "gratuita", "gratuito", "none", "na"}
        is_active = not is_free

        if end_date:
            try:
                end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
                if end_dt >= date.today():
                    is_active = is_active
                else:
                    is_active = False
            except (TypeError, ValueError):
                logger.debug("Não foi possível interpretar end_date=%r", end_date)
                end_date = None

        if is_canceled:
            if end_date:
                try:
                    end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
                    if end_dt >= date.today():
                        status = f"Cancelada (Ativa até {end_date})"
                        is_active = True
                    else:
                        status = f"Expirada em {end_date}"
                        is_active = False
                except (TypeError, ValueError):
                    status = "Cancelada"
                    is_active = False
            else:
                status = "Cancelada"
                is_active = False
        elif end_date:
            try:
                end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
                if end_dt >= date.today():
                    status = f"Ativa (Renovação em {end_date})"
                else:
                    status = f"Expirada em {end_date}"
                    is_active = False
            except (TypeError, ValueError):
                status = "Ativa"
        else:
            status = "Ativa" if is_active else "Inativa"

        return {
            "is_active": is_active,
            "status": status,
            "offer": offer,
            "start_date": start_date,
            "end_date": end_date,
            "is_canceled": is_canceled,
            "periodicity": periodicity,
            "household_size_max": household_size_max,
            "raw": sub,
        }

    async def get_user_profile(self) -> dict[str, Any]:
        raw = await self.api_call("user/get")
        self.user_info = raw.get("user", raw)
        return self.user_info

    def _modern_sig(self, epoint, params, sec):
        object_, method = epoint.split("/")
        r_sig = [object_, method]
        for key in sorted(params):
            value = params[key]
            if key not in ("request_ts", "request_sig") and isinstance(
                value, (str, int, float)
            ):
                r_sig.extend((key, str(value)))
        r_sig.extend((str(params["request_ts"]), sec))
        return hashlib.md5("".join(r_sig).encode("utf-8")).hexdigest()

    @staticmethod
    def _b64url_decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _derive_session_key(self):
        """Derive session key from Qobuz API response."""
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError(
                "cryptography nao esta disponivel. HKDF requer extensoes nativas."
            )
        salt, info = self.session_infos.split(".")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=16,
            salt=self._b64url_decode(salt),
            info=self._b64url_decode(info),
        )
        return hkdf.derive(bytes.fromhex(self.sec))

    def _unwrap_track_key(self, key_token):
        """Unwrap encrypted track key."""
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError(
                "cryptography nao esta disponivel. AES-CBC requer extensoes nativas."
            )
        _, wrapped, iv = key_token.split(".")
        decryptor = Cipher(
            algorithms.AES(self.session_key), modes.CBC(self._b64url_decode(iv))
        ).decryptor()
        padded = decryptor.update(self._b64url_decode(wrapped)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()

    async def api_call(self, epoint, **kwargs):
        """Build, sign, execute, and normalize one Qobuz API request."""
        if epoint == "user/login":
            # Prioriza token do objeto (self) sobre kwargs
            token = kwargs.get("user_auth_token") or getattr(
                self, "user_auth_token", None
            )
            if token:
                params = {
                    "user_auth_token": token,
                    "app_id": self.id,
                }
            else:
                params = {
                    "email": kwargs.get("email", ""),
                    "password": kwargs.get("pwd", ""),
                    "app_id": self.id,
                }
        elif epoint == "user/get":
            token = _resolve_user_auth_token(self, kwargs.get("user_auth_token"))
            params = {
                "app_id": self.id,
                "user_auth_token": token,
            }
        elif epoint == "track/getFileUrl":
            track_id = kwargs["id"]
            fmt_id = kwargs["fmt_id"]
            if int(fmt_id) not in (5, 6, 7, 27):
                raise InvalidQuality(
                    "ID de qualidade invalido: escolha entre 5, 6, 7 or 27"
                )
            params = {
                "track_id": track_id,
                "format_id": fmt_id,
                "intent": "stream",
            }
            unix = int(time.time())
            sec_to_use = kwargs.get("sec", self.sec)
            r_sig = f"trackgetFileUrlformat_id{fmt_id}intentstreamtrack_id{track_id}{unix}{sec_to_use}"
            params["request_ts"] = unix
            params["request_sig"] = hashlib.md5(r_sig.encode()).hexdigest()
        elif epoint == "session/start":
            params = {"profile": "qbz-1"}
            params["request_ts"] = int(time.time())
            params["request_sig"] = self._modern_sig(
                epoint, params, kwargs.get("sec", self.sec)
            )
        elif epoint == "file/url":
            track_id = kwargs["id"]
            fmt_id = kwargs["fmt_id"]
            if int(fmt_id) not in (6, 7, 27):
                raise InvalidQuality(
                    "ID de qualidade invalido: escolha entre 6, 7 or 27"
                )
            params = {
                "track_id": track_id,
                "format_id": fmt_id,
                "intent": "import",
            }
            params["request_ts"] = int(time.time())
            params["request_sig"] = self._modern_sig(
                epoint, params, kwargs.get("sec", self.sec)
            )
        elif epoint == "track/lyricsUrl":
            track_id = kwargs["track_id"]
            params = {"track_id": track_id}
            params["request_ts"] = int(time.time())
            params["request_sig"] = self._modern_sig(
                epoint, params, kwargs.get("sec", self.sec)
            )
        elif epoint == "favorite/getUserFavorites":
            unix = int(time.time())
            r_sig = "favoritegetUserFavorites" + str(unix) + kwargs.get("sec", self.sec)
            r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
            params = {
                "app_id": self.id,
                "user_auth_token": getattr(self, "uat", None),
                "user_id": getattr(self, "user_id", None),
                "type": kwargs.get("fav_type", "albums"),
                "limit": kwargs.get("limit", 100),
                "offset": kwargs.get("offset", 0),
                "request_ts": unix,
                "request_sig": r_sig_hashed,
            }
        elif epoint in (
            "playlist/getUserPlaylists",
            "playlist/getUserPlaylistIds",
        ):
            params = {"limit": kwargs.get("limit", 100)}
            user_id = kwargs.get("user_id") or getattr(self, "user_id", None)
            if user_id:
                params["user_id"] = user_id
            params["request_ts"] = int(time.time())
            params["request_sig"] = self._modern_sig(
                epoint, params, kwargs.get("sec", self.sec)
            )
        else:
            params = {"app_id": self.id}
            if getattr(self, "force_english", True):
                params["lang"] = "en"
                params["locale"] = "en_US"
            val_id = kwargs.get("id")
            for k, v in kwargs.items():
                if k not in ["id", "sec", "fmt_id"] and v is not None:
                    params[k] = v
            if epoint == "album/get":
                params["album_id"] = val_id
            elif epoint == "track/get":
                params["track_id"] = val_id
            elif epoint == "playlist/get":
                params["playlist_id"] = val_id
                params["extra"] = "tracks"
            elif epoint == "artist/get":
                params["artist_id"] = val_id
                params["extra"] = "albums"
            elif epoint == "label/get":
                params["label_id"] = val_id
                params["extra"] = "albums"
            elif epoint == "playlist/create":
                params["user_auth_token"] = getattr(self, "uat", "")
                params["name"] = kwargs.get("name", "")
                params["description"] = kwargs.get("description", "")
                params["is_public"] = "1" if kwargs.get("is_public", False) else "0"
                params["is_collaborative"] = "0"
            elif epoint == "playlist/addTracks":
                params["user_auth_token"] = getattr(self, "uat", "")
                params["playlist_id"] = kwargs.get("playlist_id", "")
                params["track_ids"] = kwargs.get("track_ids", "")

        if epoint in [
            "user/login",
            "favorite/create",
            "playlist/create",
            "playlist/addTracks",
        ]:
            method, req_kwargs = "post", {"data": params}
        elif epoint == "session/start":
            method, req_kwargs = (
                "post",
                {
                    "data": params,
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                },
            )
        else:
            method, req_kwargs = "get", {"params": params}

        mutable_endpoints = {
            "favorite/create",
            "playlist/create",
            "playlist/addTracks",
        }

        def _retryable(exc):
            if isinstance(exc, (httpx.RequestError, asyncio.TimeoutError)):
                if epoint not in mutable_endpoints:
                    return True
                return isinstance(
                    exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
                )
            if isinstance(exc, httpx.HTTPStatusError) and (
                method == "get" or epoint == "session/start"
            ):
                return (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
            return False

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=6),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                n = attempt.retry_state.attempt_number
                if n > 1:
                    logger.debug(f"Retentativa de rede em '{epoint}' ({n}/4)...")
                resp = await self.session.request(
                    method, self.base + epoint, **req_kwargs
                )
                if epoint == "user/login" and resp.status_code == 400:
                    text = resp.text
                    if "invalid" in text.lower():
                        raise AuthenticationError("Invalid email or password.")
                elif (
                    epoint
                    in [
                        "track/getFileUrl",
                        "favorite/getUserFavorites",
                        "file/url",
                        "track/lyricsUrl",
                    ]
                    and resp.status_code == 400
                ):
                    body = resp.json()
                    raise InvalidAppSecretError(
                        f"Invalid app secret: {body}.\n" + RESET
                    )
                if epoint == "user/get" and resp.status_code == 400:
                    return {}
                resp.raise_for_status()
                data = resp.json()
                return self._normalize_json_strings(data)

    async def multi_meta(self, epoint, key, id, type):
        offset = 0
        limit = 50
        while True:
            j = await self.api_call(
                epoint, id=id, offset=offset, limit=limit, type=type
            )
            res = j[type] if type and type in j else j
            items_key = "tracks" if "playlist" in epoint else "albums"
            items = res.get(items_key, {}).get("items", [])
            if not items:
                break
            yield res
            offset += len(items)
            total_available = res.get(items_key, {}).get("total", res.get(key, 0))
            if offset >= total_available:
                break

    async def get_track_meta(self, id):
        return await self.api_call("track/get", id=id)

    async def get_track_lyrics_url(self, id):
        return await self.api_call("track/lyricsUrl", track_id=id)

    async def get_track_ids_from_list(self, tracks_list: list) -> list:
        from qobuz_dl import fuzzy

        ui.emit(
            f"{CYAN}[*] Correspondencia de faixas Last.fm com o banco de dados Qobuz (correspondencia Fuzzy e modo interativo ativado)...{OFF}"
        )
        valid_track_ids = []
        AUTO_ACCEPT_THRESHOLD = 0.75
        PROMPT_THRESHOLD = 0.60
        for item in tracks_list:
            target_artist = item["artist"].lower()
            target_title = item["title"].lower()
            query = f"{item['artist']} {item['title']}"
            try:
                search_results = await self.search_tracks(query, limit=5)
                best_match_id = None
                best_match_name = ""
                highest_ratio = 0.0
                if (
                    search_results
                    and "tracks" in search_results
                    and search_results["tracks"]["items"]
                ):
                    for q_track in search_results["tracks"]["items"]:
                        q_artist_raw = q_track.get("performer", {}).get(
                            "name", "Unknown"
                        )
                        q_title_raw = q_track.get("title", "Unknown")
                        q_artist = q_artist_raw.lower()
                        q_title = q_title_raw.lower()
                        target_str = f"{target_artist} {target_title}"
                        q_str = f"{q_artist} {q_title}"
                        ratio = fuzzy.ratio(target_str, q_str)
                        if ratio > highest_ratio:
                            highest_ratio = ratio
                            best_match_id = q_track["id"]
                            best_match_name = f"{q_artist_raw} - {q_title_raw}"
                if highest_ratio >= AUTO_ACCEPT_THRESHOLD and best_match_id:
                    valid_track_ids.append(best_match_id)
                elif highest_ratio >= PROMPT_THRESHOLD and best_match_id:
                    ui.emit(
                        f"\n{YELLOW}[?] Correspondencia limetrofe detectada "
                        f"({highest_ratio * 100:.0f}% de semelhanca){OFF}"
                    )
                    ui.emit(f" Target (Last.fm): {item['artist']} - {item['title']}")
                    ui.emit(f" Found (Qobuz) : {best_match_name}")
                    choice = (
                        input(
                            f"{CYAN} Voce quer baixar esta faixa de qualquer maneira? [y/n]: {OFF}"
                        )
                        .strip()
                        .lower()
                    )
                    if choice == "y":
                        valid_track_ids.append(best_match_id)
                        ui.emit(f"{GREEN} [+] Faixa aceita manualmente.{OFF}")
                    else:
                        ui.emit(f"{RED} [-] Faixa ignorada manualmente.{OFF}")
                else:
                    ui.emit(
                        f"{YELLOW}[!] Pulando: '{query}' (A melhor combinacao foi apenas "
                        f"{highest_ratio * 100:.0f}% similar){OFF}"
                    )
            except Exception as e:
                ui.emit(f"{RED}[!] Erro ao procurar por '{query}': {e}{OFF}")
        ui.emit(
            f"\n{GREEN}[+] Combinado com sucesso {len(valid_track_ids)} "
            f"Fora de {len(tracks_list)} faixas!{OFF}"
        )
        return valid_track_ids

    async def search_by_isrc(self, isrc: str):
        if not isrc:
            return None
        try:
            results = await self.api_call(
                "catalog/search", query=isrc.strip().upper(), type="tracks", limit=1
            )
            items = (results or {}).get("tracks", {}).get("items", [])
            if items:
                return items[0].get("id")
        except Exception as e:
            logger.debug(f"Falha na pesquisa ISRC para {isrc}: {e}")
        return None

    async def search_by_upc(self, upc: str):
        if not upc:
            return None
        try:
            results = await self.api_call(
                "catalog/search", query=upc.strip(), type="albums", limit=1
            )
            items = (results or {}).get("albums", {}).get("items", [])
            if items:
                return items[0].get("id")
        except Exception as e:
            logger.debug(f"Falha na pesquisa UPC para {upc}: {e}")
        return None

    async def match_external_tracks(self, tracks: list, auto: bool = False) -> list:
        matched_ids = []
        fuzzy_queue = []
        isrc_hits = 0
        isrc_misses = 0
        for track in tracks:
            isrc = (track.get("isrc") or "").strip().upper()
            if isrc:
                qid = await self.search_by_isrc(isrc)
                if qid:
                    matched_ids.append(qid)
                    isrc_hits += 1
                    continue
                isrc_misses += 1
                fuzzy_queue.append(track)
        if isrc_hits or isrc_misses:
            logger.info(
                f"{GREEN}[+] ISRC: {isrc_hits} match(es) exato(s){OFF}"
                + (
                    f", {YELLOW}{isrc_misses} miss(es) -> fuzzy fallback{OFF}"
                    if isrc_misses
                    else ""
                )
            )
        if fuzzy_queue:
            logger.info(
                f"{CYAN}[*] Correspondencia difusa {len(fuzzy_queue)} faixa(s)...{OFF}"
            )
            fuzzy_ids = await self.get_track_ids_from_list(fuzzy_queue)
            matched_ids.extend(fuzzy_ids)
        return matched_ids

    async def search_albums(self, query, limit=20):
        try:
            return await self.api_call(
                "catalog/search", query=query, type="albums", limit=limit
            )
        except Exception:
            return {}

    async def search_tracks(self, query, limit=20):
        try:
            return await self.api_call(
                "catalog/search", query=query, type="tracks", limit=limit
            )
        except Exception:
            return {}

    async def create_qobuz_playlist(
        self, name: str, description: str = "", is_public: bool = False
    ):
        try:
            resp = await self.api_call(
                "playlist/create",
                name=name,
                description=description,
                is_public=is_public,
            )
            pl_id = str(resp.get("id") or resp.get("playlist", {}).get("id", ""))
            if pl_id:
                logger.info(
                    f"{GREEN}[+] Playlist criada no Qobuz: '{name}' (ID: {pl_id}){OFF}"
                )
            return pl_id or None
        except Exception as e:
            logger.info(f"{RED}[!] Erro ao criar playlist no Qobuz: {e}{OFF}")
            return None

    async def add_tracks_to_qobuz_playlist(
        self, playlist_id: str, track_ids: list
    ) -> bool:
        BATCH = 50
        success = True
        for i in range(0, len(track_ids), BATCH):
            batch = track_ids[i : i + BATCH]
            try:
                await self.api_call(
                    "playlist/addTracks",
                    playlist_id=playlist_id,
                    track_ids=",".join(str(t) for t in batch),
                )
            except Exception as e:
                logger.info(
                    f"{YELLOW}[!] Erro ao adicionar faixas a playlist {playlist_id}: {e}{OFF}"
                )
                success = False
        return success

    async def search_playlists(self, query, limit=20):
        try:
            return await self.api_call(
                "catalog/search", query=query, type="playlists", limit=limit
            )
        except Exception:
            return {}

    async def search_artists(self, query, limit=20):
        try:
            return await self.api_call(
                "catalog/search", query=query, type="artists", limit=limit
            )
        except Exception:
            return {}

    async def get_favorites(
        self, fav_type="albums", limit=100, offset=0, *, strict=False
    ):
        """Return one page of favorites.

        Interactive browsing keeps the historical best-effort behavior. Sync
        callers can pass ``strict=True`` so a provider failure is never
        mistaken for an empty account.
        """
        try:
            return await self.api_call(
                "favorite/getUserFavorites",
                fav_type=fav_type,
                limit=limit,
                offset=offset,
            )
        except Exception as e:
            if strict:
                raise
            logger.error(f"{RED}[!] API Error fetching favorites: {e}{OFF}")
            return {}

    async def get_all_favorites(
        self,
        fav_type="albums",
        *,
        page_size=500,
        max_pages=400,
    ):
        """Fetch every favorites page and return ``(items, reported_total)``.

        This is intentionally strict: malformed responses and network errors
        propagate so catalog synchronization cannot interpret them as mass
        removals. The provider can occasionally return an overlapping page;
        duplicate IDs are discarded and an incomplete result is rejected
        instead of being mistaken for a complete synchronization.
        """
        try:
            page_size = int(page_size)
            max_pages = int(max_pages)
        except (TypeError, ValueError) as exc:
            raise ValueError("page_size e max_pages precisam ser inteiros") from exc
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("page_size e max_pages precisam ser maiores que zero")

        items = []
        seen_ids = set()
        total = None
        offset = 0
        for _ in range(max_pages):
            response = await self.get_favorites(
                fav_type=fav_type,
                limit=page_size,
                offset=offset,
                strict=True,
            )
            block = response.get(fav_type) if isinstance(response, dict) else None
            if not isinstance(block, dict):
                raise ValueError(f"resposta sem o bloco {fav_type!r}")
            page = block.get("items") or []
            if not isinstance(page, list):
                raise ValueError(f"itens de favoritos inválidos em {fav_type!r}")
            if total is None and block.get("total") is not None:
                try:
                    total = int(block["total"])
                except (TypeError, ValueError):
                    total = None

            for item in page:
                item_id = item.get("id") if isinstance(item, dict) else None
                if item_id in (None, ""):
                    items.append(item)
                    continue
                item_key = str(item_id)
                if item_key not in seen_ids:
                    seen_ids.add(item_key)
                    items.append(item)

            if not page:
                if total is not None and len(items) < total:
                    raise RuntimeError(
                        f"paginação de favoritos incompleta: "
                        f"{len(items)} de {total} itens coletados"
                    )
                return items, total

            if total is not None and len(items) >= total:
                return items[:total], total

            offset += len(page)

            # Se o endpoint ignorou o offset ou devolveu páginas sobrepostas,
            # não continue até o limite arbitrário retornando uma lista parcial.
            if total is not None and offset >= total:
                raise RuntimeError(
                    f"paginação de favoritos incompleta: "
                    f"{len(items)} de {total} itens únicos coletados"
                )
        raise RuntimeError("paginação de favoritos excedeu o limite de segurança")

    async def get_user_playlists(self, limit=100):
        """List the authenticated user's playlists through public client APIs."""
        user_id = getattr(self, "user_id", None)
        if not user_id and isinstance(getattr(self, "user", None), dict):
            user_id = self.user.get("id")

        try:
            response = await self.api_call(
                "playlist/getUserPlaylists", limit=limit, user_id=user_id
            )
            block = response.get("playlists") if isinstance(response, dict) else None
            if isinstance(block, dict) and isinstance(block.get("items"), list):
                return response
        except Exception as exc:
            logger.debug("Falha ao listar playlists diretamente: %s", exc)

        id_response = await self.api_call(
            "playlist/getUserPlaylistIds", limit=limit, user_id=user_id
        )
        playlist_ids = (
            id_response.get("playlist_ids", []) if isinstance(id_response, dict) else []
        )
        items = []
        for playlist_id in playlist_ids[:limit]:
            try:
                playlist = await self.api_call("playlist/get", id=playlist_id)
            except Exception as exc:
                logger.debug("Falha ao buscar playlist %s: %s", playlist_id, exc)
                continue
            if isinstance(playlist, dict) and playlist.get("id") is not None:
                items.append(playlist)
        return {"playlists": {"items": items, "total": len(items)}}

    async def add_favorite_album(self, album_id):
        return await self.api_call(
            "favorite/create", album_ids=str(album_id), artist_ids="", track_ids=""
        )

    async def add_favorite_track(self, track_id):
        return await self.api_call(
            "favorite/create", track_ids=str(track_id), album_ids="", artist_ids=""
        )

    async def add_favorite(self, item_id, item_type: str):
        if item_type == "track":
            return await self.add_favorite_track(item_id)
        elif item_type == "album":
            return await self.add_favorite_album(item_id)
        else:
            raise ValueError(f"Tipo de favorito desconhecido: {item_type}")

    async def get_track_url(self, id, fmt_id, force_segments=False):
        sub_info = self.check_subscription()
        if not sub_info["is_active"]:
            raise NoActiveSubscriptionError(
                f"Assinatura inativa ou expirada ({sub_info['status']}). Download bloqueado."
            )
        if int(fmt_id) == 5:
            return await self.api_call("track/getFileUrl", id=id, fmt_id=fmt_id)
        if not force_segments:
            try:
                track = await self.api_call("track/getFileUrl", id=id, fmt_id=fmt_id)
                if "url" in track:
                    return track
            except Exception as e:
                logger.debug(f"track/getFileUrl falhou, caindo pro segmentado: {e}")
        if self.session_id is None:
            async with self._session_init_lock:
                if self.session_id is None:
                    session = await self.api_call("session/start")
                    self.session_id = session["session_id"]
                    self.session_infos = session["infos"]
                    self.session_key = self._derive_session_key()
                    self.session.headers.update({"X-Session-Id": self.session_id})
        track = await self.api_call("file/url", id=id, fmt_id=fmt_id)
        if "bits_depth" in track and "bit_depth" not in track:
            track["bit_depth"] = track["bits_depth"]
        if track.get("sampling_rate", 0) > 1000:
            track["sampling_rate"] = track["sampling_rate"] / 1000
        if "key" in track:
            track["raw_key"] = self._unwrap_track_key(track["key"])
        return track

    def get_artist_meta(self, id):
        return self.multi_meta("artist/get", "albums_count", id, None)

    def get_plist_meta(self, id):
        return self.multi_meta("playlist/get", "tracks_count", id, None)

    def get_label_meta(self, id):
        return self.multi_meta("label/get", "albums_count", id, None)

    async def get_album_meta(self, id):
        return await self.api_call("album/get", id=id)

    async def cfg_setup(self):
        for secret in self.secrets:
            try:
                await self.api_call(
                    "track/getFileUrl", id=5966783, fmt_id=5, sec=secret
                )
                self.sec = secret
                break
            except Exception:
                continue
        if not self.sec and self.secrets:
            self.sec = self.secrets[0]
        if not self.sec:
            raise InvalidAppSecretError("Nenhum segredo encontrado.")
