"""Testa `qopy.Client.auth()` e os helpers de criptografia/assinatura.

Os testes de HKDF e AES-CBC usam `cryptography` real quando suas extensoes
nativas estao disponiveis. No a-Shell/iOS, onde essas extensoes nao carregam,
apenas esses dois testes sao ignorados; os testes de assinatura, Base64 e
autenticacao continuam sendo executados normalmente.
"""

import base64
import os
from types import SimpleNamespace

import pytest

try:
    from cryptography.hazmat.primitives import hashes, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    hashes = padding = Cipher = algorithms = modes = HKDF = None

from qobuz_dl import qopy

pytestmark = pytest.mark.unit
CRYPTOGRAPHY_DISPONIVEL = HKDF is not None


def _b64url_encode_nopad(raw_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(raw_bytes).decode().rstrip("=")


# --------------------------------------------------------------------
# _modern_sig
# --------------------------------------------------------------------
class TestModernSig:
    def test_hash_bate_com_valor_calculado_independentemente(self):
        params = {
            "b": "2",
            "a": "1",
            "request_ts": 1000,
            "request_sig": "ignorado",
        }
        resultado = qopy.Client._modern_sig(
            SimpleNamespace(), "track/get", params, "segredo"
        )
        assert resultado == "f08308847ad638c9cda4f34dd3e49f5c"

    def test_ordem_de_insercao_nao_importa_so_a_chave_ordenada(self):
        params1 = {"b": "2", "a": "1", "request_ts": 1000}
        params2 = {"a": "1", "b": "2", "request_ts": 1000}
        sig1 = qopy.Client._modern_sig(
            SimpleNamespace(), "track/get", params1, "segredo"
        )
        sig2 = qopy.Client._modern_sig(
            SimpleNamespace(), "track/get", params2, "segredo"
        )
        assert sig1 == sig2

    def test_request_ts_e_request_sig_sao_excluidos_do_corpo_assinado(self):
        base = {"a": "1", "request_ts": 1000}
        com_sig_1 = {**base, "request_sig": "xxx"}
        com_sig_2 = {**base, "request_sig": "yyy-completamente-diferente"}
        sig1 = qopy.Client._modern_sig(
            SimpleNamespace(), "track/get", com_sig_1, "segredo"
        )
        sig2 = qopy.Client._modern_sig(
            SimpleNamespace(), "track/get", com_sig_2, "segredo"
        )
        assert sig1 == sig2


# --------------------------------------------------------------------
# _b64url_decode
# --------------------------------------------------------------------
class TestB64UrlDecode:
    def test_decodifica_sem_padding_explicito(self):
        assert qopy.Client._b64url_decode("YQ") == b"a"
        assert qopy.Client._b64url_decode("YWI") == b"ab"

    def test_decodifica_string_ja_com_padding_correto(self):
        assert qopy.Client._b64url_decode("YWJj") == b"abc"


# --------------------------------------------------------------------
# _derive_session_key (round-trip real contra HKDF de verdade)
# --------------------------------------------------------------------
@pytest.mark.skipif(
    not CRYPTOGRAPHY_DISPONIVEL,
    reason="cryptography com extensoes nativas indisponivel no ambiente atual",
)
class TestDeriveSessionKey:
    def test_deriva_chave_de_16_bytes_de_forma_deterministica(self):
        salt_raw = b"um-salt-de-teste"
        info_raw = b"info-de-teste"
        session_infos = (
            f"{_b64url_encode_nopad(salt_raw)}.{_b64url_encode_nopad(info_raw)}"
        )

        self = SimpleNamespace(
            session_infos=session_infos,
            sec="00112233445566778899aabbccddeeff",
            _b64url_decode=qopy.Client._b64url_decode,
        )

        chave1 = qopy.Client._derive_session_key(self)
        chave2 = qopy.Client._derive_session_key(self)

        assert len(chave1) == 16
        assert chave1 == chave2


# --------------------------------------------------------------------
# _unwrap_track_key (round-trip real: cifra aqui, desfaz na funcao)
# --------------------------------------------------------------------
@pytest.mark.skipif(
    not CRYPTOGRAPHY_DISPONIVEL,
    reason="cryptography com extensoes nativas indisponivel no ambiente atual",
)
class TestUnwrapTrackKey:
    def test_desfaz_corretamente_uma_chave_cifrada_por_aqui_mesmo(self):
        session_key = os.urandom(16)
        chave_original = b"0123456789abcdef"
        iv = os.urandom(16)

        padder = padding.PKCS7(128).padder()
        dados_com_padding = padder.update(chave_original) + padder.finalize()

        encryptor = Cipher(algorithms.AES(session_key), modes.CBC(iv)).encryptor()
        wrapped = encryptor.update(dados_com_padding) + encryptor.finalize()

        key_token = (
            f"algumcoisa.{_b64url_encode_nopad(wrapped)}.{_b64url_encode_nopad(iv)}"
        )

        self = SimpleNamespace(
            session_key=session_key,
            _b64url_decode=qopy.Client._b64url_decode,
        )

        recuperada = qopy.Client._unwrap_track_key(self, key_token)

        assert recuperada == chave_original


# --------------------------------------------------------------------
# auth()
# --------------------------------------------------------------------
def _client(**overrides):
    self = SimpleNamespace(session=SimpleNamespace(headers={}), user_info=None)
    for key, value in overrides.items():
        setattr(self, key, value)
    return self


class TestAuth:
    async def test_token_direto_pula_chamada_de_login(self):
        self = _client()

        async def api_call(epoint, **kwargs):
            assert epoint != "user/login"
            return {"user": {"id": "u1", "credential": {}, "subscription": {}}}

        self.api_call = api_call
        self.check_subscription = lambda: {"is_active": True, "status": "Ativa"}

        await qopy.Client.auth(
            self, "x@x.com", "senha-curta", user_auth_token="tok-direto"
        )

        assert self.uat == "tok-direto"
        assert self.session.headers["X-User-Auth-Token"] == "tok-direto"

    async def test_senha_longa_e_tratada_como_token_pula_login(self):
        self = _client()
        chamou_login = []

        async def api_call(epoint, **kwargs):
            if epoint == "user/login":
                chamou_login.append(True)
            return {"user": {"id": "u1", "credential": {}, "subscription": {}}}

        self.api_call = api_call
        self.check_subscription = lambda: {"is_active": True, "status": "Ativa"}
        senha_tipo_token = "x" * 61

        await qopy.Client.auth(self, "x@x.com", senha_tipo_token)

        assert self.uat == senha_tipo_token
        assert chamou_login == []

    async def test_login_normal_usa_token_devolvido_pela_api(self):
        self = _client()

        async def api_call(epoint, **kwargs):
            if epoint == "user/login":
                assert kwargs == {"email": "x@x.com", "pwd": "curta"}
                return {
                    "user": {"credential": {"parameters": {"foo": "bar"}}},
                    "user_auth_token": "tok-da-api",
                }
            return {
                "user": {
                    "id": "u9",
                    "credential": {"parameters": {"short_label": "Studio Premier"}},
                    "subscription": {},
                }
            }

        self.api_call = api_call
        self.check_subscription = lambda: {"is_active": True, "status": "Ativa"}

        await qopy.Client.auth(self, "x@x.com", "curta")

        assert self.uat == "tok-da-api"
        assert self.user_id == "u9"
        assert self.label == "Studio Premier"

    async def test_falha_ao_buscar_perfil_cai_no_fallback_sem_lancar(self):
        self = _client()

        async def api_call(epoint, **kwargs):
            if epoint == "user/login":
                return {
                    "user": {"credential": {"parameters": {"x": 1}}},
                    "user_auth_token": "tok",
                }
            raise RuntimeError("timeout de rede")

        self.api_call = api_call

        await qopy.Client.auth(self, "x@x.com", "curta")

        assert self.label == "Studio"
        assert self.user_id is None

    async def test_assinatura_inativa_nao_lanca_so_segue(self):
        self = _client()

        async def api_call(epoint, **kwargs):
            if epoint == "user/login":
                return {
                    "user": {"credential": {"parameters": {"x": 1}}},
                    "user_auth_token": "tok",
                }
            return {"user": {"id": "u1", "credential": {}, "subscription": {}}}

        self.api_call = api_call
        self.check_subscription = lambda: {"is_active": False, "status": "Expirada"}

        await qopy.Client.auth(self, "x@x.com", "curta")

        assert self.user_id == "u1"

    async def test_sem_session_nao_lanca_ao_tentar_atualizar_headers(self):
        self = _client(session=None)

        async def api_call(epoint, **kwargs):
            return {"user": {"id": "u1", "credential": {}, "subscription": {}}}

        self.api_call = api_call
        self.check_subscription = lambda: {"is_active": True, "status": "Ativa"}

        await qopy.Client.auth(self, "x@x.com", "curta", user_auth_token="tok")

        assert self.uat == "tok"
