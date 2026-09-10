"""Testa qobuz_dl/cli.py -- especificamente as duas funções que lidam com
status de assinatura da conta Qobuz: `_garantir_assinatura_ativa()` (bloqueia
comandos de download até haver uma conta ativa) e a exibição de conta dentro
de `_auth_command()` (comando `qobuz-dl auth`).

POR QUE ESTE ARQUIVO EXISTE
----------------------------
Até esta revisão, `cli.py` (1600+ linhas) não tinha NENHUM teste dedicado --
só era importado incidentalmente por outros testes (`main`,
`check_for_updates`). Isso permitiu que um bug real chegasse a produção:

    f"... {str(sub_info('periodicity', 'N/A')).capitalize()})"

Note o `sub_info(...)` -- um dict sendo CHAMADO como função, em vez de
`sub_info.get(...)`. Isso derrubava `_garantir_assinatura_ativa()` com
`TypeError: 'dict' object is not callable` toda vez que a conta configurada
estava sem assinatura ativa -- ou seja, exatamente no caminho que deveria
mostrar uma mensagem de erro amigável para o usuário. Ninguém pegou isso no
CI porque não havia teste algum passando por esse trecho.

O QUE ESTE ARQUIVO NÃO TESTA
------------------------------
Não testa a API real do Qobuz nem a lógica de parsing de `check_subscription()`
dentro de `qopy.py` (por que às vezes `offer`/`periodicity` vêm ausentes do
dict mesmo com `is_active=True` é um problema separado, de parsing da
resposta da API -- fora do escopo de `cli.py`). Aqui a suposição é sempre
"dado um `sub_info` com este formato, `cli.py` reage corretamente" --
incluindo o caso em que campos individuais vêm `None`/ausentes, que é
justamente o cenário que expôs o bug.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qobuz_dl import cli, qopy

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------
def _sub_info(**overrides):
    """`check_subscription()` real retorna um dict neste formato. Os testes
    partem de uma assinatura ATIVA e completa; cada teste sobrescreve só os
    campos que quer testar."""
    base = {
        "is_active": True,
        "status": "active",
        "offer": "studio",
        "periodicity": "monthly",
        "start_date": "2026-08-30",
        "end_date": "2026-09-29",
        "is_canceled": False,
        "household_size_max": 1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def capturar_ui(monkeypatch):
    """Substitui as funções de saída de `ui` por gravação numa lista, em vez
    de depender de capsys + tqdm.write (que pode se comportar diferente
    conforme haja ou não uma progress bar ativa). `cli.ui` e `qobuz_dl.ui`
    são o MESMO objeto de módulo, então monkeypatchar aqui também afeta as
    chamadas feitas de dentro de cli.py."""
    linhas = []

    def _grava(*args, **kwargs):
        if args:
            linhas.append(str(args[0]))
        else:
            linhas.append("")

    for nome in ("emit", "emit_always", "ok", "warn", "error", "detail"):
        monkeypatch.setattr(cli.ui, nome, _grava)

    return linhas


@pytest.fixture
def sem_input_permitido(monkeypatch):
    """Faz qualquer chamada a `input()` não esperada falhar o teste na hora,
    em vez de travar esperando stdin (que em CI vira EOFError silencioso,
    mascarando o motivo real da falha)."""

    def _explode(*args, **kwargs):
        raise AssertionError(
            "input() foi chamado sem que o teste esperasse por isso"
        )

    monkeypatch.setattr("builtins.input", _explode)


def _qobuz_falso(check_subscription_fn, initialize_client=None):
    """`_garantir_assinatura_ativa()` só usa `qobuz.client.check_subscription`
    e `qobuz.initialize_client` -- não precisa de uma instância real de
    QobuzDL."""
    return SimpleNamespace(
        client=SimpleNamespace(check_subscription=check_subscription_fn),
        initialize_client=initialize_client or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# _garantir_assinatura_ativa()
# ---------------------------------------------------------------------------
class TestGarantirAssinaturaAtiva:
    async def test_assinatura_ja_ativa_retorna_true_sem_perguntar_nada(
        self, capturar_ui, sem_input_permitido
    ):
        qobuz = _qobuz_falso(lambda: _sub_info(is_active=True))

        resultado = await cli._garantir_assinatura_ativa(qobuz)

        assert resultado is True
        # Nenhuma mensagem de erro/aviso deveria ter sido emitida -- o
        # caminho "já está tudo certo" precisa ser silencioso.
        assert not any("SEM ASSINATURA" in linha for linha in capturar_ui)

    async def test_assinatura_inativa_exibe_status_sem_quebrar_e_aceita_cancelar(
        self, capturar_ui, monkeypatch
    ):
        """Reproduz o cenário exato do bug: conta inativa, com `offer` e
        `periodicity` preenchidos (valores reais, não None) -- é precisamente
        a linha que fazia `sub_info('periodicity', 'N/A')` explodir com
        `TypeError: 'dict' object is not callable`. Se este teste passa sem
        levantar exceção, a regressão não voltou."""
        sub_info_inativo = _sub_info(
            is_active=False,
            status="inactive",
            offer="studio",
            periodicity="monthly",
        )
        qobuz = _qobuz_falso(lambda: sub_info_inativo)
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "cancelar")

        resultado = await cli._garantir_assinatura_ativa(qobuz)

        assert resultado is False
        # A linha de "Plano" precisa ter formatado offer/periodicity de
        # verdade (não travado antes de chegar lá, nem imprimido a
        # representação de uma exceção).
        linha_plano = next(l for l in capturar_ui if "Plano:" in l)
        assert "studio" in linha_plano
        assert "Monthly" in linha_plano  # .capitalize() aplicado

    async def test_campos_ausentes_caem_no_fallback_na_para_todos_os_dados(
        self, capturar_ui, monkeypatch
    ):
        """Mesmo cenário acima, mas com offer/periodicity/end_date ausentes
        (dict vindo incompleto da API) -- outro jeito comum de derrubar
        f-strings com `.get()` mal usado (ex. esquecer o `or 'N/A'` e deixar
        `None` vazar pro `.capitalize()`, que quebra em None)."""
        sub_info_incompleto = {"is_active": False, "status": "inactive"}
        qobuz = _qobuz_falso(lambda: sub_info_incompleto)
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "cancelar")

        resultado = await cli._garantir_assinatura_ativa(qobuz)

        assert resultado is False
        linha_plano = next(l for l in capturar_ui if "Plano:" in l)
        assert "N/A" in linha_plano

    async def test_usuario_atualiza_credenciais_e_conta_fica_ativa_sai_do_laco(
        self, capturar_ui, monkeypatch
    ):
        """1ª leitura de check_subscription() -> inativa. Usuário aceita
        atualizar (Enter). `_auth_command` é mockado (testado à parte). 2ª
        leitura (depois de `initialize_client`) -> ativa. O laço deve sair
        SEM pedir um segundo `input()`."""
        chamadas = {"n": 0}

        def _check_subscription():
            chamadas["n"] += 1
            return _sub_info(is_active=(chamadas["n"] >= 2))

        auth_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(cli, "_auth_command", auth_mock)
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")  # Enter

        qobuz = _qobuz_falso(_check_subscription)
        resultado = await cli._garantir_assinatura_ativa(qobuz)

        assert resultado is True
        assert chamadas["n"] == 2
        auth_mock.assert_awaited_once_with(cli.CONFIG_FILE, update_credentials=True)
        qobuz.initialize_client.assert_awaited_once()

    async def test_falha_ao_reinicializar_client_continua_no_laco_sem_crash(
        self, capturar_ui, monkeypatch
    ):
        """Se as credenciais novas forem inválidas, `initialize_client()`
        estoura uma exceção de rede/autenticação. O laço precisa capturar
        isso, tratar como "ainda inativa" e voltar a perguntar -- não deixar
        a exceção subir e derrubar o programa."""
        respostas = iter(["", "cancelar"])  # 1ª tentativa, depois desiste

        async def _initialize_client_falha(*a, **k):
            raise ConnectionError("simulado: token inválido")

        qobuz = _qobuz_falso(
            lambda: _sub_info(is_active=False),
            initialize_client=_initialize_client_falha,
        )
        monkeypatch.setattr(cli, "_auth_command", AsyncMock(return_value=False))
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respostas))

        resultado = await cli._garantir_assinatura_ativa(qobuz)

        assert resultado is False
        assert any("Falha ao validar a nova conta" in l for l in capturar_ui)


# ---------------------------------------------------------------------------
# _auth_command() -- exibição do relatório de conta (show_json=False)
# ---------------------------------------------------------------------------
def _user_info(**overrides):
    base = {
        "id": 14528994,
        "publicId": "qobuz:user:OTDgqljtQbfAb",
        "firstname": "",
        "lastname": "",
        "display_name": "Eduardo",
        "email": "usuario@example.com",
        "login": "usuario@example.com",
        "country": "BR",
        "zone": "BR",
        "store": "BR-pt",
        "language_code": "pt",
        "birthdate": "2003-02-26",
        "age": 23,
        "genre": "male",
        "creation_date": "2026-08-30",
        "store_features": {"streaming": True, "lyrics": True},
        "credential": {"description": "Assinante"},
        "last_update": {},
    }
    base.update(overrides)
    return base


@pytest.fixture
def cliente_qobuz_falso(monkeypatch):
    """Substitui `qopy.Client.create` (importado localmente dentro de
    `_auth_command`) por uma fábrica que devolve um cliente falso
    configurável por teste."""

    def _instala(user_info, sub_info):
        cliente = SimpleNamespace(
            get_user_profile=AsyncMock(return_value=user_info),
            check_subscription=lambda: sub_info,
            close=AsyncMock(),
        )
        monkeypatch.setattr(qopy.Client, "create", AsyncMock(return_value=cliente))
        return cliente

    return _instala


@pytest.fixture
def config_com_token(tmp_path):
    """config.ini próprio deste grupo de testes, com `auth_token` já
    preenchido e `disable_keyring = true` (evita qualquer tentativa real de
    Keyring, que seria não-determinística em CI).

    O config.ini padrão do conftest.py (usado pelos outros testes) NÃO tem
    `auth_token` -- de propósito, pra simular "primeira execução". Só que
    isso faz `_auth_command()` entrar no branch de "sem token, precisa
    pedir credencial" (`if update_credentials or not token:`) mesmo quando
    o teste passa `update_credentials=False`. Sem este fixture, o teste
    acaba testando o formulário de credenciais por acidente, não o
    relatório de conta que é o alvo real aqui."""
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[qobuz]\n"
        "email = usuario@example.com\n"
        "app_id = 1\n"
        "secrets = a\n"
        "auth_token = token-falso-para-teste\n"
        "disable_keyring = true\n",
        encoding="utf-8",
    )
    return str(cfg)


class TestAuthCommandExibicao:
    async def test_assinatura_ativa_com_campos_ausentes_nao_quebra(
        self, capturar_ui, sem_input_permitido, cliente_qobuz_falso, config_com_token
    ):
        """Reproduz o relatório que o usuário viu na prática: `is_active`
        True, mas `offer`/`periodicity` ausentes no dict retornado por
        `check_subscription()` (problema de parsing em qopy.py, fora do
        escopo deste teste) -- `cli.py` precisa exibir 'N/A' graciosamente
        em vez de quebrar, e NÃO deve pedir troca de credenciais (conta já
        está ativa) nem o formulário de credenciais (já existe um token
        configurado).
        """
        cliente_qobuz_falso(
            _user_info(),
            {"is_active": True, "status": "active", "end_date": "2026-09-29"},
        )

        resultado = await cli._auth_command(config_com_token, update_credentials=False)

        assert resultado is True
        linha_plano = next(l for l in capturar_ui if "Plano / Oferta:" in l)
        assert "N/A" in linha_plano
        assert not any("AVISO DE ASSINATURA INATIVA" in l for l in capturar_ui)

    async def test_assinatura_inativa_pergunta_e_usuario_recusa_trocar(
        self, capturar_ui, monkeypatch, cliente_qobuz_falso, config_com_token
    ):
        cliente_qobuz_falso(
            _user_info(),
            _sub_info(is_active=False, status="inactive"),
        )
        # Só UMA pergunta é esperada aqui ("Deseja alterar e-mail/token
        # agora?"), já que o token já existe e a função não deveria pedir
        # o formulário de credenciais de novo. Um segundo input() (o que
        # aconteceria por engano, se `_auth_command` caísse no branch de
        # "sem token") faz o teste falhar explicitamente, em vez de
        # mascarar o motivo com uma resposta genérica reaproveitada.
        respostas = iter(["n"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respostas))

        resultado = await cli._auth_command(config_com_token, update_credentials=False)

        assert resultado is False
        assert any("AVISO DE ASSINATURA INATIVA" in l for l in capturar_ui)
