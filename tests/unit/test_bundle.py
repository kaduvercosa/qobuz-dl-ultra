"""Testa `Bundle.get_app_id()`/`Bundle.get_secrets()` de qobuz_dl/bundle.py
-- sem nenhuma requisição HTTP real. `Bundle.__new__(Bundle)` pula o
`__init__` (que faz `httpx.Client().get(...)` de verdade), e `._bundle`
é setado manualmente com um trecho de JS fake que casa com os regexes
que essas duas funções usam pra extrair app_id/secrets.

O payload base64 usado em test_get_secrets foi gerado e validado à parte
(roundtrip conferido) antes de ir pro teste -- não foi digitado à mão.
"""

from collections import OrderedDict

import pytest

from qobuz_dl.bundle import Bundle

pytestmark = pytest.mark.unit


def _bundle_sem_rede(bundle_js: str) -> Bundle:
    instancia = Bundle.__new__(Bundle)
    instancia._bundle = bundle_js
    return instancia


class TestGetAppId:
    def test_extrai_app_id_de_9_digitos(self):
        bundle = _bundle_sem_rede(
            'algum lixo antes production:{api:{appId:"123456789",'
            f'appSecret:"{"a" * 32}"}} lixo depois'
        )

        assert bundle.get_app_id() == "123456789"

    def test_sem_match_lanca_not_implemented_error(self):
        bundle = _bundle_sem_rede("bundle.js sem nenhum appId reconhecivel")

        with pytest.raises(NotImplementedError):
            bundle.get_app_id()

    def test_app_id_com_menos_de_9_digitos_nao_casa(self):
        # O regex exige exatamente \d{9} -- um id mais curto não deve
        # ser aceito parcialmente.
        bundle = _bundle_sem_rede(
            f'production:{{api:{{appId:"1234",appSecret:"{"a" * 32}"'
        )

        with pytest.raises(NotImplementedError):
            bundle.get_app_id()


class TestGetSecrets:
    def test_decodifica_um_secret_de_um_timezone(self):
        # Payload base64 de "meusegredoteste" (b"meusegredoteste"),
        # verificado à parte: base64 = seed[:10] + info[10:] (20 chars),
        # + 44 chars de "lixo" de padding que get_secrets() descarta.
        bundle_js = (
            'a.initialSeed("bWV1c2Vncm",window.utimezone.berlin);'
            'name:"algumacoisa/Berlin",info:"Vkb3Rlc3Rl",'
            f'extras:"{"X" * 44}"'
        )
        bundle = _bundle_sem_rede(bundle_js)

        secrets = bundle.get_secrets()

        assert secrets == OrderedDict([("berlin", "meusegredoteste")])

    def test_sem_nenhum_initial_seed_devolve_dict_vazio(self):
        bundle = _bundle_sem_rede("bundle.js sem nenhum initialSeed")

        assert bundle.get_secrets() == OrderedDict()
