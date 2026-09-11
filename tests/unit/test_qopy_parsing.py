"""Testa qobuz_dl/qopy.py -- mas só a parte segura de testar: parsing de
resposta e normalização de dados.

ESCOPO DELIBERADAMENTE LIMITADO
---------------------------------
`qopy.py` também tem métodos de assinatura/derivação de chave de sessão
(`_modern_sig`, `_derive_session_key`, `_unwrap_track_key`) e a autenticação
de verdade contra a API do Qobuz (`auth`, `Client.create`). Nenhum desses
entra aqui, de propósito -- não é sobre esses métodos serem difíceis de
testar, é escopo que não vou aprofundar. O que este arquivo cobre é lógica
de negócio pura que não depende de rede nem de nada sensível:

- `check_subscription()`: le `self.user_info` (um dict já em memória) e
  decide texto/estado de assinatura -- nenhuma chamada de rede, nenhuma
  credencial.
- `_normalize_json_strings()`: normalização recursiva de string/unicode em
  qualquer JSON -- utilitário genérico, sem relação com autenticação.

`Client()` (sem `.create()`) é seguro de instanciar direto nos testes: só
seta atributos em memória, não abre sessão httpx nem faz nenhuma
requisição.
"""

import unicodedata
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from tenacity import wait_none

from qobuz_dl import qopy
from qobuz_dl.qopy import Client


def _cliente_com(user_info):
    c = Client()
    c.user_info = user_info
    return c


async def test_api_call_repete_get_apos_erro_transitorio(monkeypatch):
    client = Client()
    client.id = "123"
    client.base = "https://api.invalid/"
    client.uat = "token"
    responses = [
        httpx.Response(503, request=httpx.Request("GET", "https://api.invalid/")),
        httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request("GET", "https://api.invalid/"),
        ),
    ]
    client.session = type(
        "Session",
        (),
        {"request": AsyncMock(side_effect=responses)},
    )()
    monkeypatch.setattr(qopy, "wait_exponential", lambda **kwargs: wait_none())

    assert await client.api_call("track/get", id="10") == {"ok": True}
    assert client.session.request.await_count == 2


async def test_api_call_nao_repete_post_mutavel_apos_erro_de_leitura(monkeypatch):
    client = Client()
    client.id = "123"
    client.base = "https://api.invalid/"
    client.uat = "token"
    request = httpx.Request("POST", "https://api.invalid/playlist/create")
    client.session = type(
        "Session",
        (),
        {"request": AsyncMock(side_effect=httpx.ReadError("falhou", request=request))},
    )()
    monkeypatch.setattr(qopy, "wait_exponential", lambda **kwargs: wait_none())

    with pytest.raises(httpx.ReadError):
        await client.api_call(
            "playlist/create", name="Teste", description="", is_public=False
        )

    assert client.session.request.await_count == 1


async def test_create_fecha_sessao_quando_autenticacao_falha(monkeypatch):
    session = type("Session", (), {"aclose": AsyncMock(), "headers": {}})()
    monkeypatch.setattr(qopy.httpx, "AsyncClient", lambda **kwargs: session)

    async def fail_auth(self, *args, **kwargs):
        raise RuntimeError("auth falhou")

    monkeypatch.setattr(Client, "auth", fail_auth)

    with pytest.raises(RuntimeError, match="auth falhou"):
        await Client.create("mail", "pwd", "123", ["secret"])

    session.aclose.assert_awaited_once()


# --------------------------------------------------------------------
# check_subscription
# --------------------------------------------------------------------
class TestCheckSubscriptionSemDados:
    def test_user_info_vazio_cai_no_default_inativo(self):
        resultado = _cliente_com({}).check_subscription()
        assert resultado["is_active"] is False
        assert resultado["status"] == "Inativa / Sem Assinatura"
        assert resultado["household_size_max"] == 1
        assert resultado["raw"] == {}

    def test_subscription_que_nao_e_dict_tambem_cai_no_default(self):
        # Resposta inesperada da API (ex.: subscription=null virou string
        # em algum bug de outro sistema) não pode derrubar o parsing.
        resultado = _cliente_com(
            {"subscription": "algo-nao-e-dict"}
        ).check_subscription()
        assert resultado["is_active"] is False
        assert resultado["status"] == "Inativa / Sem Assinatura"


class TestCheckSubscriptionComEndDate:
    def test_end_date_no_futuro_e_ativa(self):
        futuro = (date.today() + timedelta(days=30)).isoformat()
        sub = {"offer": "premium", "end_date": futuro, "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is True
        assert resultado["status"].startswith("Ativa (Renovação em")

    def test_cancelada_mas_ainda_no_periodo_pago_continua_ativa(self):
        # Cancelar não desativa na hora -- continua valendo até end_date.
        futuro = (date.today() + timedelta(days=5)).isoformat()
        sub = {"offer": "premium", "end_date": futuro, "is_canceled": True}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is True
        assert resultado["status"].startswith("Cancelada (Ativa até")

    def test_end_date_no_passado_e_expirada(self):
        passado = (date.today() - timedelta(days=1)).isoformat()
        sub = {"offer": "premium", "end_date": passado, "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is False
        assert resultado["status"].startswith("Expirada em")

    def test_end_date_hoje_ainda_conta_como_ativa(self):
        # >= today, não > today -- o dia de expiração ainda é válido.
        hoje = date.today().isoformat()
        sub = {"offer": "premium", "end_date": hoje, "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is True

    def test_end_date_em_formato_invalido_nao_derruba_e_usa_fallback(self):
        sub = {
            "offer": "premium",
            "end_date": "isto-nao-e-uma-data",
            "is_canceled": False,
        }
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        # Cai no except: is_active = not is_canceled
        assert resultado["is_active"] is True
        assert resultado["status"] == "Ativa"

    def test_end_date_invalido_e_cancelada_fica_inativa_no_fallback(self):
        sub = {"offer": "premium", "end_date": "lixo", "is_canceled": True}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is False
        assert resultado["status"] == "Cancelada"


class TestCheckSubscriptionSemEndDate:
    """Planos sem end_date (ex.: vitalício) caem no branch baseado só na
    oferta -- sem data nenhuma pra comparar."""

    def test_oferta_paga_sem_cancelamento_e_ativa(self):
        sub = {"offer": "premium", "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is True
        assert resultado["status"] == "Ativa"
        assert resultado["end_date"] is None

    def test_oferta_free_e_inativa(self):
        sub = {"offer": "free", "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is False

    def test_oferta_paga_mas_cancelada_e_inativa(self):
        sub = {"offer": "premium", "is_canceled": True}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["is_active"] is False


class TestCheckSubscriptionCamposAuxiliares:
    def test_offer_ausente_vira_n_a(self):
        # {} vazio é falsy em Python e cairia no branch "sem assinatura
        # nenhuma" (not sub) -- por isso um campo qualquer pra manter o
        # dict não-vazio e chegar de fato na lógica de `offer`.
        resultado = _cliente_com(
            {"subscription": {"is_canceled": False}}
        ).check_subscription()
        assert resultado["offer"] == "N/a"  # "n/a".capitalize()

    def test_household_size_max_default_e_1(self):
        sub = {"offer": "premium", "is_canceled": False}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["household_size_max"] == 1

    def test_household_size_max_e_repassado_quando_presente(self):
        sub = {"offer": "premium", "is_canceled": False, "household_size_max": 6}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["household_size_max"] == 6

    def test_start_date_invalido_mantem_string_original(self):
        sub = {
            "offer": "premium",
            "is_canceled": False,
            "start_date": "data-quebrada",
        }
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        # ValueError no strptime -- mantém o valor bruto em vez de quebrar.
        assert resultado["start_date"] == "data-quebrada"

    def test_raw_preserva_o_dict_original_da_api(self):
        sub = {"offer": "premium", "is_canceled": False, "campo_desconhecido": 123}
        resultado = _cliente_com({"subscription": sub}).check_subscription()
        assert resultado["raw"] == sub


# --------------------------------------------------------------------
# _normalize_json_strings
# --------------------------------------------------------------------
class TestNormalizeJsonStrings:
    def test_reticencias_soltas_viram_caractere_unico(self):
        c = Client()
        assert c._normalize_json_strings("Album incompleto...") == "Album incompleto…"

    def test_reticencias_dentro_de_url_nao_sao_tocadas(self):
        # O check `"://" not in obj` existe especificamente pra não
        # corromper URLs que por acaso tenham "..." no path/query.
        c = Client()
        url = "https://example.com/path...with/dots"
        assert c._normalize_json_strings(url) == url

    def test_dict_e_normalizado_recursivamente(self):
        c = Client()
        entrada = {"titulo": "Faixa incompleta...", "numero": 3}
        resultado = c._normalize_json_strings(entrada)
        assert resultado["titulo"] == "Faixa incompleta…"
        assert resultado["numero"] == 3

    def test_lista_e_normalizada_recursivamente(self):
        c = Client()
        resultado = c._normalize_json_strings(["a...", "b...", 5])
        assert resultado == ["a…", "b…", 5]

    def test_dict_aninhado_dentro_de_lista_tambem_e_normalizado(self):
        c = Client()
        entrada = [{"nome": "Artista..."}]
        resultado = c._normalize_json_strings(entrada)
        assert resultado[0]["nome"] == "Artista…"

    def test_tipos_nao_string_dict_lista_passam_direto(self):
        c = Client()
        assert c._normalize_json_strings(42) == 42
        assert c._normalize_json_strings(None) is None
        assert c._normalize_json_strings(True) is True
        assert c._normalize_json_strings(3.14) == 3.14

    def test_unicode_decomposto_e_normalizado_pra_forma_composta(self):
        # "e" + acento combinante (U+0301) precisa virar "é" precomposto
        # (NFC) -- strings decompostas vindas da API quebram comparação
        # e busca por igualdade em outras partes do projeto.
        c = Client()
        decomposto = "cafe\u0301"  # "café" com o acento como char separado
        resultado = c._normalize_json_strings(decomposto)
        assert resultado == "café"
        assert resultado == unicodedata.normalize("NFC", decomposto)
        assert len(resultado) == 4  # forma composta: c-a-f-é (não 5 chars)
