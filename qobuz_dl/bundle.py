# ==============================================================================
# MÓDULO: bundle.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Responsável por raspar (scraping) e extrair dinamicamente o App ID
#            e os segredos de autenticação (secrets/app_secret) a partir do
#            JavaScript (bundle.js) do Web Player oficial do Qobuz.
# BASEADO EM: Spoofbuz (DashLt)
#
# ONDE PROCURAR quando precisar mexer em algo:
#   - "Qobuz mudou o link do bundle.js no HTML de login" -> _BUNDLE_URL_REGEX
#   - "App ID não é mais encontrado"                     -> _APP_ID_REGEX
#   - "Secrets não decodificam mais / vieram vazios"      -> get_secrets()
#     (é o método mais frágil do arquivo: depende de 3 regex + a lógica de
#     reordenação/concatenação abaixo, tudo isso muda quando o Qobuz atualiza
#     o bundle.js)
#   - Uso síncrono  -> Bundle()  (__init__)
#   - Uso assíncrono -> Bundle.create() (classmethod)
# ==============================================================================

import base64
import logging
import re
from collections import OrderedDict
import httpx

# Configuração do logger local para registrar eventos e depuração (debug)
logger = logging.getLogger(__name__)

# ==============================================================================
# EXPRESSÕES REGULARES (REGEX) PARA PARSING DO BUNDLE JS
# ==============================================================================

# Regex para encontrar as chamadas de semente inicial vinculadas a timezones no JS.
# Exemplo no JS: a.initialSeed("abcde...", window.utimezone.berlin)
_SEED_TIMEZONE_REGEX = re.compile(
    r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",window\.utimezone\.(?P<timezone>[a-z]+)\)'
)

# Template de regex para extrair os blocos "info" e "extras" correspondentes a cada timezone.
# Os timezones serão injetados dinamicamente via .format().
_INFO_EXTRAS_REGEX = r'name:"\w+/(?P<timezone>{timezones})",info:"(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"'

# Regex para capturar o App ID de 9 dígitos da configuração de produção da API no bundle.
# Exemplo no JS: production:{api:{appId:"123456789",appSecret:"..."
_APP_ID_REGEX = re.compile(
    r'production:{api:{appId:"(?P<app_id>\d{9})",appSecret:"\w{32}"'
)

# URL base do player web do Qobuz
_BASE_URL = "https://play.qobuz.com"

# Regex para extrair o caminho relativo do arquivo bundle.js presente no HTML da página de login.
# Exemplo capturado: /resources/7.1.0-b012/bundle.js
_BUNDLE_URL_REGEX = re.compile(
    r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>'
)


# ==============================================================================
# CLASSE PRINCIPAL: Bundle
# ==============================================================================
class Bundle:
    """
    Obtém o arquivo JavaScript do Qobuz e decodifica chaves de API e segredos.
    """

    def __init__(self):
        """
        Inicializador síncrono.
        1. Acessa a página de login para descobrir a URL do bundle.js atual.
        2. Faz o download do bundle.js.
        3. Armazena o código-fonte em self._bundle para processamento posterior.
        """
        with httpx.Client() as client:
            logger.debug("Obtendo página de login para localizar o bundle.js")
            response = client.get(f"{_BASE_URL}/login")
            response.raise_for_status()

            # Localiza a tag <script src="..."> do bundle
            bundle_url_match = _BUNDLE_URL_REGEX.search(response.text)
            if not bundle_url_match:
                # DICA DE MANUTENÇÃO: Se disparar este erro, o Qobuz mudou a estrutura
                # do link da tag <script> no HTML da página de login.
                raise NotImplementedError(
                    "URL do Bundle não encontrada no HTML de login")

            bundle_url = bundle_url_match.group(1)

            logger.debug(f"Baixando arquivo bundle: {_BASE_URL + bundle_url}")
            response = client.get(_BASE_URL + bundle_url)
            response.raise_for_status()

            # Guarda todo o código JS do bundle em memória
            self._bundle = response.text

    @classmethod
    async def create(cls):
        """
        Factory method assíncrono para inicialização com suporte a asyncio/httpx.AsyncClient.

        ATENÇÃO / NOTA DE MANUTENÇÃO:
        No código original, este método apenas baixava o login e colocava o HTML em self._bundle.
        Para paridade total com o __init__, certifique-se de que ele também extraia e baixe o bundle.js.
        """
        # cls.__new__ pula o __init__ normal (que é síncrono e faria I/O
        # bloqueante) -- por isso o corpo abaixo repete manualmente os passos
        # do __init__, mas usando await/AsyncClient.
        instance = cls.__new__(cls)
        async with httpx.AsyncClient() as client:
            logger.debug("Obtendo página de login de forma assíncrona")
            response = await client.get(f"{_BASE_URL}/login", timeout=15.0)
            response.raise_for_status()

            # Procura a URL do bundle no HTML
            bundle_url_match = _BUNDLE_URL_REGEX.search(response.text)
            if bundle_url_match:
                bundle_url = bundle_url_match.group(1)
                bundle_res = await client.get(_BASE_URL + bundle_url, timeout=30.0)
                bundle_res.raise_for_status()
                instance._bundle = bundle_res.text
            else:
                # Fallback silencioso: se não achar o link do bundle, guarda o
                # HTML da página de login mesmo assim (get_app_id/get_secrets
                # vão simplesmente falhar em não encontrar nada, sem crashar aqui).
                instance._bundle = response.text

        return instance

    def get_app_id(self) -> str:
        """
        Extrai o App ID oficial da API contido dentro do bundle.

        Retorna:
            str: O App ID numérico (ex: '9 dígitos').

        DICA DE MANUTENÇÃO:
            Se o Qobuz alterar o formato do objeto de configuração 'production:{api:{appId:...}}',
            este regex precisará ser atualizado.
        """
        match = _APP_ID_REGEX.search(self._bundle)
        if not match:
            raise NotImplementedError("Falha ao localizar o APP ID no bundle")

        return match.group("app_id")

    def get_secrets(self) -> OrderedDict:
        """
        Reconstrói e decodifica a tabela de segredos (secrets) ofuscados no JS.

        Como funciona a ofuscação do Qobuz:
        1. Encontra os pares iniciais (seed + timezone).
        2. Reorganiza a ordem das chaves (move_to_end do 2º item para o início).
        3. Busca blocos 'info' e 'extras' para cada timezone.
        4. Concatena 'seed + info + extras'.
        5. Remove os últimos 44 caracteres (lixo/padding de ofuscação).
        6. Decodifica a string resultante em Base64 para obter o segredo em texto puro (UTF-8).

        Retorna:
            OrderedDict: Dicionário contendo { 'timezone': 'secret_decodificado' }
        """
        logger.debug("Iniciando extração e decodificação dos segredos (secrets)")

        # 1. Encontra todas as ocorrências de initialSeed
        # secrets[timezone] começa como uma LISTA de 1 item (o seed) e depois
        # vira lista de 3 itens (seed, info, extras) no passo 4, antes de virar
        # a string final decodificada no passo 5.
        seed_matches = _SEED_TIMEZONE_REGEX.finditer(self._bundle)
        secrets = OrderedDict()

        for match in seed_matches:
            seed, timezone = match.group("seed", "timezone")
            secrets[timezone] = [seed]

        # 2. Ajuste na ordem dos pares de chaves.
        # NOTA: essa reordenação (mover o 2º timezone encontrado para o
        # início do dict) reflete uma peculiaridade da forma como o Qobuz
        # embaralha os segredos no bundle.js -- é herdado do projeto Spoofbuz
        # e não é óbvio pela leitura; se os secrets pararem de decodificar
        # corretamente (erro de padding no base64 do passo 6), este é um dos
        # primeiros lugares a revisar.
        keypairs = list(secrets.items())
        if len(keypairs) > 1:
            secrets.move_to_end(keypairs[1][0], last=False)

        # 3. Monta o regex dinâmico com os nomes das timezones com inicial maiúscula
        info_extras_regex = _INFO_EXTRAS_REGEX.format(
            timezones="|".join([timezone.capitalize() for timezone in secrets])
        )

        # 4. Associa os valores de 'info' e 'extras' para cada timezone
        info_extras_matches = re.finditer(info_extras_regex, self._bundle)
        for match in info_extras_matches:
            timezone, info, extras = match.group("timezone", "info", "extras")
            secrets[timezone.lower()] += [info, extras]

        # 5. Concatena os pedaços, remove o sufixo de 44 chars e decodifica de Base64
        # Os 44 caracteres finais são "lixo" de ofuscação inserido pelo Qobuz
        # (não fazem parte do segredo real); se o Qobuz mudar esse tamanho de
        # padding, o base64 abaixo vai falhar com erro de padding inválido.
        for secret_pair in secrets:
            raw_b64 = "".join(secrets[secret_pair])[:-44]
            secrets[secret_pair] = base64.standard_b64decode(raw_b64).decode("utf-8")

        return secrets
