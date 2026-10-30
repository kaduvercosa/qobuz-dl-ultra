#!/usr/bin/env python3
"""Gera notas de versão no formato Keep a Changelog a partir do git.

Dois modos:

* **Com IA** (padrão quando existe a chave de pelo menos um provedor no ambiente): lê o
  diff de cada commit e pede a uma IA para descrever **o que realmente mudou**, agrupado
  em Adicionado / Modificado / Corrigido etc. Vários provedores gratuitos formam uma
  cadeia: se um falha (limite, fora do ar, chave inválida), o próximo assume. Também
  lista os arquivos alterados entre as duas versões.
* **Sem IA** (`--sem-ia`, sem token, ou se a IA falhar): usa só os títulos e corpos
  dos commits, como antes. A geração nunca quebra por causa da IA.

Exemplos:
    python tools/gerar_changelog.py
    python tools/gerar_changelog.py --para v2.6.0
    python tools/gerar_changelog.py --de v2.5.0 --para v2.6.0
    python tools/gerar_changelog.py --para v2.6.0 --resumido
    python tools/gerar_changelog.py --para v2.6.0 --sem-ia
    python tools/gerar_changelog.py --para v2.6.0 --exigir-ia --modelo openai/gpt-4.1

Provedores (todos com API compatível com OpenAI; a ordem padrão é a da tabela):

    provedor     chave                modelo padrão (troque com CHANGELOG_MODELO_<NOME>)
    gemini       GEMINI_API_KEY       gemini-3.8-flash   (--modelo / CHANGELOG_MODELO)
    groq         GROQ_API_KEY         openai/gpt-oss-120b (--modelo-reserva)
    openrouter   OPENROUTER_API_KEY   openai/gpt-oss-120b:free
    mistral      MISTRAL_API_KEY      mistral-small-latest
    cerebras     CEREBRAS_API_KEY     gpt-oss-120b
    github       GITHUB_TOKEN         openai/gpt-4.1-mini
    ollama       OLLAMA_MODEL         (local, sem chave: o nome do modelo já ativa)

    GEMINI_API_KEY=... GROQ_API_KEY=... python tools/gerar_changelog.py --para v2.6.0
    python tools/gerar_changelog.py --provedores mistral,groq   # ordem e escolha
    CHANGELOG_PROVEDORES=groq,openrouter python tools/gerar_changelog.py

Só entram na cadeia os provedores que têm chave. Os planos gratuitos mudam com
frequência: confira os limites de cada um antes de depender dele.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import NamedTuple, TypedDict

_FS = "\x1f"
_RS = "\x1e"

# --- IA (cadeia de provedores compatíveis com OpenAI) -----------------------

API_URL = "https://models.github.ai/inference/chat/completions"  # legado
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_BASE_URL = "https://api.groq.com/openai/v1/"
MODELO_PADRAO = "gemini-3.8-flash"
MODELO_RESERVA_PADRAO = "openai/gpt-oss-120b"
USER_AGENT = "qobuz-dl-ultra-changelog/1.0"
PAUSAS_NAO_JSON = (5.0, 20.0)


class EspecProvedor(NamedTuple):
    """Como falar com um provedor: endereço, variável da chave e modelo padrão."""

    nome: str
    rotulo: str
    base_url: str
    chave_env: str
    modelo: str
    # Teto da resposta. Planos gratuitos com janela pequena (8K) pedem menos.
    max_tokens: int = 5000


# A ordem aqui é a ordem padrão da cadeia. Gemini e Groq vêm primeiro (são os que o
# projeto já usava); os de janela pequena no plano gratuito ficam para o fim.
PROVEDORES: dict[str, EspecProvedor] = {
    "gemini": EspecProvedor(
        "gemini", "Gemini", GEMINI_BASE_URL, "GEMINI_API_KEY", MODELO_PADRAO
    ),
    "groq": EspecProvedor(
        "groq", "Groq", GROQ_BASE_URL, "GROQ_API_KEY", MODELO_RESERVA_PADRAO
    ),
    "openrouter": EspecProvedor(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "openai/gpt-oss-120b:free",
    ),
    "mistral": EspecProvedor(
        "mistral",
        "Mistral",
        "https://api.mistral.ai/v1",
        "MISTRAL_API_KEY",
        "mistral-small-latest",
    ),
    "cerebras": EspecProvedor(
        "cerebras",
        "Cerebras",
        "https://api.cerebras.ai/v1",
        "CEREBRAS_API_KEY",
        "gpt-oss-120b",
        max_tokens=3000,
    ),
    "github": EspecProvedor(
        "github",
        "GitHub Models",
        "https://models.github.ai/inference",
        "GITHUB_TOKEN",
        "openai/gpt-4.1-mini",
        max_tokens=3500,
    ),
}
ORDEM_PADRAO = tuple(PROVEDORES)
OLLAMA_URL_PADRAO = "http://localhost:11434/v1"

# Limite de caracteres de contexto (metadados + diff) por requisição. Os planos
# gratuitos limitam os tokens de entrada, então o diff é enxugado
# e os commits são agrupados em lotes que cabem nesse limite.
LIMITE_PROMPT = 14000
LIMITE_ARQUIVO = 3000
LIMITE_ARQUIVO_TESTE = 800
LIMITE_CORPO = 1500
LIMITE_ARQUIVOS_NO_CONTEXTO = 40
LIMITE_ARQUIVOS_NA_LISTA = 80
AVISO_CORTE = 40  # espaço reservado para o aviso "[... N linhas omitidas ...]"

MAX_TENTATIVAS = 3
PAUSA_ENTRE_LOTES = 4.0  # segundos; o plano gratuito limita requisições por minuto

# Arquivos que só atrapalham a leitura (gerados, caches, o próprio changelog).
IGNORAR_NO_DIFF = [
    "CHANGELOG.md",
    "SKILL.md",
    ".pytest_cache",
    ".ruff_cache",
    "mypy-baseline.txt",
    "*.lock",
]

RUIDO = [
    r"^style:\s*correç(ões|oes) automátic",
    r"^Merge pull request",
    r"^Merge branch",
    r"^Auto-Sync by Working Copy$",
    r"pytest cache",
    r"nodeids",
    r"^Bump version to ",
]

CATEGORIAS = [
    (
        "Adicionado",
        [
            r"^feat(?:\([^)]*\))?[!:]",
            r"^Add\b",
            r"^Create\b",
            r"^Novo\b",
            r"^Implement",
        ],
    ),
    (
        "Modificado",
        [
            r"^refactor(?:\([^)]*\))?[!:]",
            r"^Refactor\b",
            r"^update(?:\([^)]*\))?[!:]",
            r"^Update\b",
            r"^Modify\b",
            r"^Change\b",
            r"^Configure\b",
            r"^Rename\b",
            r"^deps(?:\([^)]*\))?[!:]",
            r"^chore\(deps\)",
            r"^ci:\s*bump the .* group",
            r"^ci:",
            r".*workflow.*",
            r"^docs(?:\([^)]*\))?[!:]",
            r".*README.*",
            r".*documenta",
        ],
    ),
    (
        "Removido",
        [
            r"^Remove\b",
            r"^Delete\b",
            r"^Drop\b",
        ],
    ),
    (
        "Corrigido",
        [
            r"^fix(?:\([^)]*\))?[!:]",
            r"^Fix\b",
            r"^Corrig",
            r"^Hotfix\b",
            r"^Bugfix\b",
        ],
    ),
    (
        "Obsoleto",
        [
            r"^deprecat",
            r"^Deprecat",
            r"^Obsolet",
        ],
    ),
    (
        "Segurança",
        [
            r"^security(?:\([^)]*\))?[!:]",
            r"^Security\b",
            r"^CVE[-:]",
            r".*vulnerabil",
        ],
    ),
]

_NOMES_CATEGORIAS = [rotulo for rotulo, _ in CATEGORIAS]

PROMPT_SISTEMA = """\
Você escreve as notas de versão (changelog) do qobuz-dl-ultra, um downloader de \
música do Qobuz em Python (linha de comando e interface de terminal), em português \
do Brasil.

Você recebe commits com título, corpo, lista de arquivos e diff. Descreva o que \
REALMENTE mudou, lendo o diff -- não repita só o título do commit.

Regras:
- Cada item diz o que mudou e, quando o diff mostrar, o efeito para quem usa: \
comando, flag, opção do config.ini, tela ou comportamento. Escreva nomes de flags, \
comandos, opções e arquivos entre `crases`.
- Use somente o que aparece no diff, no título e no corpo. Não invente \
funcionalidades, números nem motivos. Se algo não estiver claro, seja mais genérico.
- Uma mudança funcional por item. Um commit pode originar vários itens.
- Separe funcionalidades independentes mesmo quando pertencem ao mesmo arquivo.
- Junte commits somente quando descrevem a MESMA mudança funcional.
- Explique comportamento anterior e novo quando houver evidência no diff.
- Classifique correções em Corrigido, sem misturá-las com novidades.
- Não transforme nomes de funções isolados em benefícios que o diff não comprova.
- Ignore o que não afeta quem usa nem quem desenvolve (formatação, imports \
reordenados, comentários, renomear variável).
- Refatoração interna, testes e CI relevantes entram em "Modificado", em um item \
curto.
- Cada item tem de 1 a 3 frases (até uns 350 caracteres), sem emojis e sem markdown \
além de `crases`.
- Categorias permitidas, escritas exatamente assim: Adicionado, Modificado, \
Removido, Corrigido, Obsoleto, Segurança.
- Em "commits", coloque os hashes curtos (como aparecem em "Commit ...") que \
originaram o item.

Responda SOMENTE com um JSON neste formato, sem texto fora dele:
{"resumo": "1 a 2 frases com o panorama destes commits", \
"itens": [{"categoria": "Adicionado", "texto": "...", "commits": ["abc1234"]}]}
"""

PROMPT_CONSOLIDAR = """\
Você recebe os itens de changelog de uma versão do qobuz-dl-ultra, escritos em \
partes separadas. Una os itens repetidos ou que tratam da mesma coisa, mantenha a \
categoria correta e escreva um "resumo" de 2 a 3 frases sobre a versão inteira, em \
português do Brasil.

Regras:
- Não acrescente nenhuma informação nova e não apague mudanças relevantes.
- Preserve uma mudança funcional por item: NÃO agrupe assuntos independentes.
- Não reduza a quantidade de itens apenas para encurtar a versão.
- Una apenas duplicatas da MESMA mudança, mantendo detalhes e efeitos.
- Separe correções de funcionalidades adicionadas.
- Ao unir itens, junte também as listas de "commits".
- Cada item continua com 1 a 3 frases e sem markdown além de `crases`.
- Categorias permitidas: Adicionado, Modificado, Removido, Corrigido, Obsoleto, \
Segurança.

Responda SOMENTE com um JSON neste formato:
{"resumo": "...", "itens": [{"categoria": "Adicionado", "texto": "...", \
"commits": ["abc1234"]}]}
"""


class Item(TypedDict):
    """Um item do changelog descrito pela IA."""

    categoria: str
    texto: str
    commits: list[str]


class ErroIA(Exception):
    """Falha ao falar com o provedor de IA ou ao entender a resposta."""

    def __init__(self, codigo: int | None, detalhe: str) -> None:
        super().__init__(f"HTTP {codigo}: {detalhe}" if codigo else detalhe)
        self.codigo = codigo
        self.detalhe = detalhe

    @property
    def fatal(self) -> bool:
        """Sem permissão ou modelo inexistente: insistir em outros lotes é inútil."""
        return self.codigo in (401, 403, 404)


class Provedor(NamedTuple):
    """Um provedor pronto para uso: já com a chave e o modelo escolhidos."""

    nome: str
    base_url: str
    modelo: str
    token: str
    max_tokens: int = 5000


class ConfigIA(NamedTuple):
    """Configuração da IA (NamedTuple: carrega sem registrar o módulo, como nos testes)."""

    token: str
    modelo: str = MODELO_PADRAO
    limite_prompt: int = LIMITE_PROMPT
    modelo_reserva: str | None = None
    base_url: str = GEMINI_BASE_URL
    base_url_reserva: str = GROQ_BASE_URL
    token_reserva: str | None = None
    max_tokens: int = 5000
    # Demais provedores da cadeia, tentados em ordem depois do principal/reserva.
    adicionais: tuple[Provedor, ...] = ()


def _log(mensagem: str) -> None:
    print(f"[changelog] {mensagem}", file=sys.stderr)


# --- git --------------------------------------------------------------------


def _rodar_git(*args: str) -> str:
    resultado = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return resultado.stdout.strip()


def _tag_anterior(ref: str) -> str | None:
    try:
        return _rodar_git("describe", "--tags", "--abbrev=0", f"{ref}^")
    except subprocess.CalledProcessError:
        return None


def _e_ruido(mensagem: str) -> bool:
    return any(re.search(padrao, mensagem, re.IGNORECASE) for padrao in RUIDO)


def _categorizar(mensagem: str) -> str:
    for rotulo, padroes in CATEGORIAS:
        if any(re.search(padrao, mensagem, re.IGNORECASE) for padrao in padroes):
            return rotulo
    return "Modificado"


def _url_base_repositorio() -> str | None:
    try:
        remote = _rodar_git("config", "--get", "remote.origin.url")
    except subprocess.CalledProcessError:
        return None

    match = re.search(r"github\.com[:/]{1,2}([^/]+)/(.+?)(?:\.git)?$", remote)
    if not match:
        return None
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def _resumo_estatisticas(commit_hash: str) -> str | None:
    try:
        saida = _rodar_git("show", "--shortstat", "--format=", commit_hash)
    except subprocess.CalledProcessError:
        return None

    linhas = saida.splitlines()
    if not linhas:
        return None

    linha = linhas[-1]
    arquivos = re.search(r"(\d+) files? changed", linha)
    adicionadas = re.search(r"(\d+) insertions?\(\+\)", linha)
    removidas = re.search(r"(\d+) deletions?\(-\)", linha)
    if not arquivos:
        return None

    partes = [f"{arquivos.group(1)} arquivo(s) alterado(s)"]
    if adicionadas:
        partes.append(f"+{adicionadas.group(1)}")
    if removidas:
        partes.append(f"-{removidas.group(1)}")
    return ", ".join(partes)


def _coletar_commits(ref_de: str, ref_para: str) -> list[dict[str, str]]:
    formato = f"%h{_FS}%s{_FS}%b{_RS}"
    log_bruto = _rodar_git(
        "log",
        f"--pretty=format:{formato}",
        f"{ref_de}..{ref_para}",
    )

    commits = []
    for registro in log_bruto.split(_RS):
        registro = registro.strip("\n")
        if not registro.strip():
            continue
        campos = registro.split(_FS)
        if len(campos) < 2:
            continue
        commits.append(
            {
                "hash": campos[0].strip(),
                "titulo": campos[1].strip(),
                "corpo": campos[2].strip() if len(campos) > 2 else "",
            }
        )
    return commits


def _pathspec_sem_ruido() -> list[str]:
    return ["--", ".", *[f":(exclude){padrao}" for padrao in IGNORAR_NO_DIFF]]


def _numstat(*comando: str) -> list[tuple[str, str, str]]:
    """Roda `git show/diff --numstat` e devolve (adicionadas, removidas, caminho)."""
    try:
        saida = _rodar_git(*comando, "--numstat", *_pathspec_sem_ruido())
    except subprocess.CalledProcessError:
        return []
    arquivos = []
    for linha in saida.splitlines():
        partes = linha.split("\t", 2)
        if len(partes) == 3:
            arquivos.append((partes[0], partes[1], partes[2]))
    return arquivos


def _diff_do_commit(commit_hash: str) -> str:
    try:
        return _rodar_git(
            "show",
            "--format=",
            "--no-color",
            "--no-ext-diff",
            "--unified=2",
            "-M",
            commit_hash,
            *_pathspec_sem_ruido(),
        )
    except subprocess.CalledProcessError:
        return ""


# --- preparo do contexto para a IA ------------------------------------------


def _e_teste(caminho: str) -> bool:
    nome = caminho.rsplit("/", 1)[-1]
    return (
        caminho.startswith("tests/") or "/tests/" in caminho or nome.startswith("test_")
    )


def _blocos_diff(diff: str) -> list[str]:
    return [parte for parte in re.split(r"(?m)^(?=diff --git )", diff) if parte.strip()]


def _caminho_do_bloco(bloco: str) -> str:
    achado = re.match(r"diff --git a/.+? b/(.+)", bloco)
    return achado.group(1).strip() if achado else "?"


def _limpar_bloco(bloco: str) -> str:
    """Tira do cabeçalho as linhas sem valor (index, ---, +++), sem tocar nos hunks."""
    linhas = bloco.splitlines()
    inicio_hunks = next(
        (i for i, linha in enumerate(linhas) if linha.startswith("@@")), len(linhas)
    )
    cabecalho = [
        linha
        for linha in linhas[:inicio_hunks]
        if not linha.startswith(("index ", "similarity index", "--- ", "+++ "))
    ]
    return "\n".join(cabecalho + linhas[inicio_hunks:])


def _repartir_limite(tamanhos: list[int], limite: int) -> list[int]:
    """Divide `limite` entre os arquivos sem deixar um arquivo grande engolir tudo.

    Arquivos pequenos ficam inteiros; o que sobra é repartido igualmente entre os
    maiores (cada um com a sua cota, nunca acima do seu tamanho).
    """
    cotas = [0] * len(tamanhos)
    restante = max(limite, 0)
    faltam = len(tamanhos)
    for indice in sorted(range(len(tamanhos)), key=lambda i: tamanhos[i]):
        cotas[indice] = min(tamanhos[indice], restante // faltam)
        restante -= cotas[indice]
        faltam -= 1
    return cotas


def _enxugar_diff(diff: str, limite: int) -> str:
    """Reduz o diff para caber em `limite` caracteres, mostrando todos os arquivos.

    Código-fonte vem antes dos testes; cada arquivo recebe uma cota (arquivos de teste
    têm teto menor) e o que passar dela é cortado com um aviso de linhas omitidas.
    """
    blocos = sorted(_blocos_diff(diff), key=lambda b: _e_teste(_caminho_do_bloco(b)))
    if not blocos:
        return ""
    textos = [_limpar_bloco(bloco) for bloco in blocos]
    tamanhos = [
        min(
            len(texto),
            LIMITE_ARQUIVO_TESTE
            if _e_teste(_caminho_do_bloco(bloco))
            else LIMITE_ARQUIVO,
        )
        for bloco, texto in zip(blocos, textos)
    ]
    # Reserva espaço para o aviso de corte de cada arquivo.
    cotas = _repartir_limite(tamanhos, limite - AVISO_CORTE * len(blocos))

    saida: list[str] = []
    for texto, cota in zip(textos, cotas):
        if len(texto) > cota:
            corte = texto[:cota].rsplit("\n", 1)[0]
            restantes = texto.count("\n") - corte.count("\n")
            texto = f"{corte}\n[... {restantes} linhas omitidas ...]"
        saida.append(texto)
    return "\n".join(saida)


def _contexto_commit(commit: dict[str, str], limite: int) -> str:
    arquivos = _numstat("show", "--format=", commit["hash"])
    lista = [
        f"{caminho} (+{adicionadas}/-{removidas})"
        for adicionadas, removidas, caminho in arquivos[:LIMITE_ARQUIVOS_NO_CONTEXTO]
    ]
    if len(arquivos) > LIMITE_ARQUIVOS_NO_CONTEXTO:
        lista.append(f"... e mais {len(arquivos) - LIMITE_ARQUIVOS_NO_CONTEXTO}")

    cabecalho = [f"### Commit {commit['hash']}", f"Título: {commit['titulo']}"]
    if commit["corpo"]:
        cabecalho.append(f"Corpo: {commit['corpo'][:LIMITE_CORPO]}")
    if lista:
        cabecalho.append("Arquivos: " + "; ".join(lista))
    cabecalho.append("Diff:")
    topo = "\n".join(cabecalho)

    espaco = max(limite - len(topo) - 50, 1000)
    diff = _enxugar_diff(_diff_do_commit(commit["hash"]), espaco)
    return f"{topo}\n{diff}".rstrip()


def _montar_lotes(
    contextos: list[tuple[dict[str, str], str]], limite: int
) -> list[list[tuple[dict[str, str], str]]]:
    lotes: list[list[tuple[dict[str, str], str]]] = []
    atual: list[tuple[dict[str, str], str]] = []
    tamanho = 0
    for item in contextos:
        if atual and tamanho + len(item[1]) > limite:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(item)
        tamanho += len(item[1]) + 2
    if atual:
        lotes.append(atual)
    return lotes


# --- chamada ao provedor de IA -----------------------------------------------


def _espera_retry_after(erro: urllib.error.HTTPError) -> float:
    try:
        return min(float(erro.headers.get("Retry-After", "")), 60.0)
    except (TypeError, ValueError):
        return 15.0


class SessaoIA:
    """Estado isolado por geração; nunca persiste entre releases."""

    def __init__(self) -> None:
        # Posição na cadeia a partir da qual se tenta: depois que um provedor falha,
        # os próximos lotes já começam no que funcionou.
        self.indice = 0
        self.usados: list[str] = []

    @property
    def reserva_ativa(self) -> bool:
        return self.indice > 0


_SESSAO_IA: ContextVar[SessaoIA | None] = ContextVar("sessao_changelog", default=None)

_ROTULOS_POR_ENDERECO = {
    "api.groq.com": "Groq",
    "generativelanguage.googleapis.com": "Gemini",
    "openrouter.ai": "OpenRouter",
    "api.mistral.ai": "Mistral",
    "api.cerebras.ai": "Cerebras",
    "models.github.ai": "GitHub Models",
    "localhost": "Ollama",
    "127.0.0.1": "Ollama",
}


def _rotulo_modelo(config: ConfigIA) -> str:
    for endereco, rotulo in _ROTULOS_POR_ENDERECO.items():
        if endereco in config.base_url:
            return f"{rotulo}: {config.modelo}"
    return f"{config.base_url}: {config.modelo}"


def _registrar_sucesso(config: ConfigIA) -> None:
    rotulo = _rotulo_modelo(config)
    sessao = _SESSAO_IA.get()
    if sessao is not None and rotulo not in sessao.usados:
        sessao.usados.append(rotulo)
    _log(f"IA: resposta recebida de {rotulo}")


def _cadeia(config: ConfigIA) -> list[tuple[str, ConfigIA]]:
    """Provedores na ordem de tentativa: principal, reserva e adicionais."""
    principal = config._replace(
        modelo=config.modelo.strip() or MODELO_PADRAO, adicionais=()
    )
    cadeia = [("principal", principal)]
    reserva = (config.modelo_reserva or "").strip()
    if reserva and reserva.lower() != "nenhum" and config.token_reserva:
        cadeia.append(
            (
                "reserva",
                principal._replace(
                    token=config.token_reserva.strip(),
                    modelo=reserva,
                    base_url=config.base_url_reserva,
                    modelo_reserva=None,
                    token_reserva=None,
                ),
            )
        )
    for extra in config.adicionais:
        cadeia.append(
            (
                "reserva",
                principal._replace(
                    token=extra.token.strip(),
                    modelo=extra.modelo,
                    base_url=extra.base_url,
                    max_tokens=extra.max_tokens,
                    modelo_reserva=None,
                    token_reserva=None,
                ),
            )
        )
    return cadeia


def _chamar_modelo(
    mensagens: list[dict[str, str]], config: ConfigIA, timeout: float = 120.0
) -> str:
    cadeia = _cadeia(config)
    sessao = _SESSAO_IA.get()
    inicio = min(sessao.indice, len(cadeia) - 1) if sessao is not None else 0

    tentados: list[ConfigIA] = []
    erros: list[ErroIA] = []
    descricoes: list[str] = []
    for posicao in range(inicio, len(cadeia)):
        papel, atual = cadeia[posicao]
        if any(
            t.modelo == atual.modelo and t.base_url == atual.base_url for t in tentados
        ):
            continue  # mesmo modelo no mesmo endereço: repetir não adianta
        tentados.append(atual)
        try:
            texto = _chamar_modelo_unico(mensagens, atual, timeout)
        except ErroIA as erro:
            if erro.codigo == 413:
                raise  # entrada grande demais: quem chamou divide o lote
            erros.append(erro)
            descricoes.append(f"{papel} {atual.modelo}: {erro}")
            proximos = [
                c
                for _, c in cadeia[posicao + 1 :]
                if not any(
                    t.modelo == c.modelo and t.base_url == c.base_url for t in tentados
                )
            ]
            if proximos:
                _log(
                    f"modelo {atual.modelo} falhou ({erro}); "
                    f"tentando reserva {proximos[0].modelo}"
                )
            continue
        if sessao is not None and posicao > sessao.indice:
            sessao.indice = posicao
            _log(
                f"IA: mantendo {atual.modelo} nos próximos lotes e na consolidação "
                "desta geração"
            )
        _registrar_sucesso(atual)
        return texto

    if len(erros) == 1:
        raise erros[0]
    raise ErroIA(erros[-1].codigo, "; ".join(descricoes))


def _chamar_modelo_unico(
    mensagens: list[dict[str, str]], config: ConfigIA, timeout: float = 120.0
) -> str:
    if not config.token.strip():
        raise ErroIA(401, f"chave ausente para {config.modelo}")
    corpo: dict[str, object] = {
        "model": config.modelo,
        "messages": mensagens,
        "temperature": 0.2,
        "max_tokens": config.max_tokens,
        "response_format": {"type": "json_object"},
    }
    url = config.base_url.rstrip("/") + "/chat/completions"
    cabecalhos = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config.token.strip()}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        ultima = tentativa == MAX_TENTATIVAS
        pedido = urllib.request.Request(
            url,
            data=json.dumps(corpo).encode("utf-8"),
            headers=cabecalhos,
            method="POST",
        )
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                bruto = resposta.read()
                status = getattr(resposta, "status", None)
                tipo = getattr(resposta, "headers", {}).get("Content-Type", "")
            try:
                dados = json.loads(bruto)
            except json.JSONDecodeError as erro:
                amostra = bruto[:300].decode("utf-8", "replace")
                detalhe = (
                    f"modelo {config.modelo}: resposta que não é JSON "
                    f"(status {status}, tipo {tipo!r}, url {url}, "
                    f"{len(bruto)} bytes): {amostra!r}; "
                    "o provedor não informou a causa da resposta inválida"
                )
                if ultima:
                    raise ErroIA(None, detalhe) from erro
                _log(f"{detalhe}; tentativa {tentativa}/{MAX_TENTATIVAS}")
                corpo.pop("response_format", None)
                time.sleep(PAUSAS_NAO_JSON[min(tentativa - 1, 1)])
                continue
            conteudo = dados["choices"][0]["message"]["content"]
            if not isinstance(conteudo, str) or not conteudo.strip():
                raise ValueError("resposta de chat sem conteúdo textual")
            return conteudo
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:2000]
            for segredo in (config.token, config.token_reserva):
                if segredo:
                    detalhe = detalhe.replace(segredo, "[CHAVE OCULTA]")
            if (
                erro.code == 400
                and "response_format" in detalhe
                and "response_format" in corpo
                and not ultima
            ):
                corpo.pop("response_format")
                continue
            if erro.code in (429, 500, 502, 503, 504) and not ultima:
                espera = (
                    _espera_retry_after(erro)
                    if erro.code == 429
                    else PAUSAS_NAO_JSON[min(tentativa - 1, 1)]
                )
                _log(
                    f"modelo {config.modelo}: HTTP {erro.code}; aguardando {espera:.0f}s"
                )
                time.sleep(espera)
                continue
            raise ErroIA(erro.code, detalhe) from erro
        except (
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as erro:
            if ultima:
                raise ErroIA(None, f"{type(erro).__name__}: {erro}") from erro
            time.sleep(2.0 * tentativa)
    raise ErroIA(None, "sem resposta da IA")


def _extrair_json(bruto: str) -> dict[str, object]:
    texto = bruto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    for candidato in (texto, texto[texto.find("{") : texto.rfind("}") + 1]):
        try:
            dados = json.loads(candidato)
        except json.JSONDecodeError:
            continue
        if isinstance(dados, dict):
            return dados
    raise ErroIA(None, "a IA não devolveu um JSON válido")


def _limpar_texto(texto: str) -> str:
    texto = " ".join(texto.split())
    # "@nome" em notas de release marca (e notifica) uma pessoa de verdade no GitHub.
    return re.sub(r"@(?=\w)", "@\u200b", texto)


def _normalizar_categoria(valor: object) -> str:
    alvo = str(valor).strip().lower()
    for rotulo in _NOMES_CATEGORIAS:
        if rotulo.lower() == alvo:
            return rotulo
    return "Modificado"


def _normalizar_resposta(
    dados: dict[str, object], hashes_validos: list[str]
) -> tuple[str, list[Item]]:
    resumo = _limpar_texto(str(dados.get("resumo") or ""))
    itens: list[Item] = []
    brutos = dados.get("itens")
    if not isinstance(brutos, list):
        return resumo, itens

    for bruto in brutos:
        if not isinstance(bruto, dict):
            continue
        texto = _limpar_texto(str(bruto.get("texto") or ""))
        if not texto:
            continue
        citados: list[str] = []
        lista = bruto.get("commits")
        for citado in lista if isinstance(lista, list) else []:
            citado = str(citado).strip()
            achado = next(
                (
                    h
                    for h in hashes_validos
                    if citado and (h.startswith(citado) or citado.startswith(h))
                ),
                None,
            )
            if achado and achado not in citados:
                citados.append(achado)
        itens.append(
            Item(
                categoria=_normalizar_categoria(bruto.get("categoria")),
                texto=texto,
                commits=citados,
            )
        )
    return resumo, itens


def _resumir_lote(
    lote: list[tuple[dict[str, str], str]], config: ConfigIA
) -> tuple[str, list[Item]]:
    hashes = [commit["hash"] for commit, _ in lote]
    contexto = "\n\n".join(texto for _, texto in lote)
    mensagens = [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": f"Commits para descrever:\n\n{contexto}"},
    ]
    try:
        bruto = _chamar_modelo(mensagens, config)
    except ErroIA as erro:
        if erro.codigo != 413:
            raise
        # Entrada grande demais para o plano: divide o lote ao meio e tenta de novo.
        if len(lote) > 1:
            meio = len(lote) // 2
            resumo_a, itens_a = _resumir_lote(lote[:meio], config)
            resumo_b, itens_b = _resumir_lote(lote[meio:], config)
            return f"{resumo_a} {resumo_b}".strip(), itens_a + itens_b
        commit, texto = lote[0]
        if len(texto) <= 1500:
            raise
        _log(f"commit {commit['hash']} grande demais; reduzindo o diff")
        return _resumir_lote([(commit, texto[: len(texto) // 2])], config)
    return _normalizar_resposta(_extrair_json(bruto), hashes)


def _consolidar(
    resumo: str, itens: list[Item], config: ConfigIA
) -> tuple[str, list[Item]]:
    """Une itens repetidos entre lotes e escreve o resumo da versão inteira."""
    hashes = sorted({h for item in itens for h in item["commits"]})
    entrada = json.dumps({"resumo": resumo, "itens": itens}, ensure_ascii=False)
    bruto = _chamar_modelo(
        [
            {"role": "system", "content": PROMPT_CONSOLIDAR},
            {"role": "user", "content": entrada},
        ],
        config,
    )
    novo_resumo, novos = _normalizar_resposta(_extrair_json(bruto), hashes)

    citados_antes = set(hashes)
    citados_depois = {h for item in novos for h in item["commits"]}
    if not novos or not citados_antes <= citados_depois:
        # A união perdeu mudanças: mantém os itens originais e aproveita só o resumo.
        return novo_resumo or resumo, itens
    return novo_resumo or resumo, novos


def _descrever_com_ia(
    commits: list[dict[str, str]], config: ConfigIA
) -> tuple[str, list[Item]]:
    contextos = [(c, _contexto_commit(c, config.limite_prompt)) for c in commits]
    lotes = _montar_lotes(contextos, config.limite_prompt)

    itens: list[Item] = []
    resumos: list[str] = []
    falhas = 0
    ultimo_erro = ""
    for numero, lote in enumerate(lotes, start=1):
        if numero > 1:
            time.sleep(PAUSA_ENTRE_LOTES)
        _log(f"IA: lote {numero}/{len(lotes)} ({len(lote)} commit(s))")
        try:
            resumo, novos = _resumir_lote(lote, config)
        except ErroIA as erro:
            if erro.fatal:
                raise
            falhas += 1
            ultimo_erro = str(erro)
            _log(
                f"IA falhou no lote {numero}: {erro}; usando os títulos desses commits"
            )
            continue
        if resumo:
            resumos.append(resumo)
        itens.extend(novos)

    if falhas == len(lotes):
        raise ErroIA(
            None, f"a IA falhou em todos os lotes (último erro: {ultimo_erro})"
        )

    resumo_final = " ".join(resumos)
    if len(lotes) > 1 and itens:
        time.sleep(PAUSA_ENTRE_LOTES)
        _log("IA: unindo os itens de todos os lotes")
        try:
            resumo_final, itens = _consolidar(resumo_final, itens, config)
        except ErroIA as erro:
            if erro.fatal:
                raise
            _log(f"IA não conseguiu unir os lotes: {erro}; mantendo os itens separados")
            resumo_final = resumos[0] if len(resumos) == 1 else ""
    return resumo_final, itens


# --- saída ------------------------------------------------------------------


def _referencia(hash_curto: str, url_base: str | None) -> str:
    if url_base:
        return f"[`{hash_curto}`]({url_base}/commit/{hash_curto})"
    return f"`{hash_curto}`"


def _linhas_por_titulo(
    grupos: dict[str, list[dict[str, str]]], url_base: str | None, detalhado: bool
) -> list[str]:
    linhas: list[str] = []
    for categoria, _ in CATEGORIAS:
        itens = grupos.get(categoria)
        if not itens:
            continue

        linhas.append(f"### {categoria}")
        linhas.append("")
        for item in itens:
            linhas.append(f"- {item['titulo']} ({_referencia(item['hash'], url_base)})")

            if detalhado and item["corpo"]:
                for texto in item["corpo"].splitlines():
                    texto = texto.strip()
                    if texto:
                        linhas.append(f"  {texto}")

            if detalhado:
                estatisticas = _resumo_estatisticas(item["hash"])
                if estatisticas:
                    linhas.append(f"  _{estatisticas}._")
            linhas.append("")
    return linhas


def _linhas_da_ia(
    resumo: str,
    itens: list[Item],
    sem_item: list[dict[str, str]],
    url_base: str | None,
) -> list[str]:
    """Monta as seções a partir dos itens da IA.

    `sem_item` são commits que a IA não citou em nenhum item: entram pelo título,
    para nenhuma mudança sumir do changelog.
    """
    linhas: list[str] = []
    if resumo:
        linhas.extend([f"> {resumo}", ""])

    por_categoria: dict[str, list[str]] = {}
    for item in itens:
        refs = ", ".join(_referencia(h, url_base) for h in item["commits"])
        sufixo = f" ({refs})" if refs else ""
        por_categoria.setdefault(item["categoria"], []).append(
            f"- {item['texto']}{sufixo}"
        )
    for commit in sem_item:
        por_categoria.setdefault(_categorizar(commit["titulo"]), []).append(
            f"- {_limpar_texto(commit['titulo'])} ({_referencia(commit['hash'], url_base)})"
        )

    for categoria in _NOMES_CATEGORIAS:
        if categoria in por_categoria:
            linhas.extend([f"### {categoria}", "", *por_categoria[categoria], ""])
    return linhas


def _secao_arquivos(ref_de: str, ref_para: str, url_base: str | None) -> list[str]:
    arquivos = _numstat("diff", ref_de, ref_para)
    if not arquivos:
        return []

    def numero(valor: str) -> int:
        return int(valor) if valor.isdigit() else 0

    total_mais = sum(numero(a) for a, _, _ in arquivos)
    total_menos = sum(numero(r) for _, r, _ in arquivos)
    linhas = [
        "<details>",
        f"<summary>Arquivos alterados ({len(arquivos)}) · "
        f"+{total_mais} −{total_menos}</summary>",
        "",
    ]
    for adicionadas, removidas, caminho in arquivos[:LIMITE_ARQUIVOS_NA_LISTA]:
        if adicionadas == "-":
            linhas.append(f"- `{caminho}` (binário)")
        else:
            linhas.append(f"- `{caminho}` (+{adicionadas} −{removidas})")
    if len(arquivos) > LIMITE_ARQUIVOS_NA_LISTA:
        linhas.append(
            f"- … e mais {len(arquivos) - LIMITE_ARQUIVOS_NA_LISTA} arquivo(s)"
        )
    linhas.extend(["", "</details>", ""])
    if url_base:
        linhas.extend(
            [
                f"[Comparar `{ref_de}...{ref_para}`]({url_base}/compare/{ref_de}...{ref_para})",
                "",
            ]
        )
    return linhas


def gerar_changelog(
    ref_de: str,
    ref_para: str,
    detalhado: bool = True,
    versao: str | None = None,
    data_lancamento: str | None = None,
    ia: ConfigIA | None = None,
    exigir_ia: bool = False,
) -> str:
    marcador = _SESSAO_IA.set(SessaoIA())
    try:
        return _gerar_changelog_impl(
            ref_de, ref_para, detalhado, versao, data_lancamento, ia, exigir_ia
        )
    finally:
        _SESSAO_IA.reset(marcador)


def _gerar_changelog_impl(
    ref_de: str,
    ref_para: str,
    detalhado: bool = True,
    versao: str | None = None,
    data_lancamento: str | None = None,
    ia: ConfigIA | None = None,
    exigir_ia: bool = False,
) -> str:
    commits = _coletar_commits(ref_de, ref_para)
    url_base = _url_base_repositorio() if detalhado else None
    grupos: dict[str, list[dict[str, str]]] = {}
    relevantes: list[dict[str, str]] = []
    ignorados = 0

    for commit in commits:
        if _e_ruido(commit["titulo"]):
            ignorados += 1
            continue
        relevantes.append(commit)
        grupos.setdefault(_categorizar(commit["titulo"]), []).append(commit)

    versao = versao or ref_para
    data_lancamento = data_lancamento or date.today().isoformat()
    linhas = [f"## [{versao}] - {data_lancamento}", ""]

    resultado_ia: tuple[str, list[Item]] | None = None
    if ia and detalhado and relevantes:
        try:
            resultado_ia = _descrever_com_ia(relevantes, ia)
            sessao = _SESSAO_IA.get()
            usados = sessao.usados if sessao is not None else []
            modo = f"IA ({'; '.join(usados) if usados else ia.modelo})"
            if sessao is not None and sessao.reserva_ativa:
                modo += " (reserva mantida após falha do principal)"
        except ErroIA as erro:
            if exigir_ia:
                raise
            _log(f"IA indisponível ({erro}); usando os títulos dos commits")
            modo = f"títulos dos commits (IA indisponível: {erro})"
    elif ia and not detalhado:
        modo = "títulos dos commits (modo resumido)"
    else:
        modo = "títulos dos commits"

    if resultado_ia is not None:
        resumo, itens = resultado_ia
        citados = {h for item in itens for h in item["commits"]}
        sem_item = [c for c in relevantes if c["hash"] not in citados]
        linhas.extend(_linhas_da_ia(resumo, itens, sem_item, url_base))
    else:
        linhas.extend(_linhas_por_titulo(grupos, url_base, detalhado))

    if not any(grupos.values()):
        linhas.extend(
            [
                "_Sem mudanças relevantes registradas._",
                "",
            ]
        )

    if detalhado:
        linhas.extend(_secao_arquivos(ref_de, ref_para, url_base))

    total = len(commits)
    listados = total - ignorados
    # Dentro de um comentário HTML, "-->" (ou "--!>") fecharia o comentário antes da hora.
    modo_seguro = re.sub(r"--(?=!?>)", "- -", " ".join(modo.split()))[:400]
    linhas.extend(
        [
            "<!--",
            f"Intervalo analisado: {ref_de}..{ref_para}",
            f"Commits analisados: {total}; listados: {listados}; "
            f"ignorados: {ignorados}.",
            f"Modo: {modo_seguro}",
            "-->",
        ]
    )
    return "\n".join(linhas).rstrip() + "\n"


def _nome_ordem(texto: str) -> list[str]:
    """Lê uma lista de provedores separada por vírgula (ou espaço)."""
    return [parte.lower() for parte in re.split(r"[,\s]+", texto) if parte]


def _montar_provedores(
    ambiente: dict[str, str] | os._Environ[str],
    ordem: list[str],
    *,
    modelo_gemini: str = MODELO_PADRAO,
    modelo_groq: str = MODELO_RESERVA_PADRAO,
    base_gemini: str = GEMINI_BASE_URL,
    base_groq: str = GROQ_BASE_URL,
) -> list[Provedor]:
    """Provedores da cadeia, na ordem pedida, só os que têm chave no ambiente.

    O modelo de cada um vem de `CHANGELOG_MODELO_<NOME>`; o do Gemini e o do Groq
    também aceitam `--modelo` e `--modelo-reserva` (como antes dos outros provedores).
    """
    desconhecidos = [n for n in ordem if n not in PROVEDORES and n != "ollama"]
    if desconhecidos:
        validos = ", ".join([*PROVEDORES, "ollama"])
        raise ValueError(
            f"provedor desconhecido: {', '.join(desconhecidos)} (válidos: {validos})"
        )
    cadeia: list[Provedor] = []
    for nome in dict.fromkeys(ordem):  # sem repetir, mantendo a ordem
        if nome == "ollama":
            modelo = ambiente.get("OLLAMA_MODEL", "").strip()
            if modelo:
                url = ambiente.get("OLLAMA_BASE_URL", "").strip() or OLLAMA_URL_PADRAO
                cadeia.append(Provedor("ollama", url, modelo, "ollama", 4000))
            continue
        espec = PROVEDORES[nome]
        chave = ambiente.get(espec.chave_env, "").strip()
        if not chave:
            continue
        modelo = ambiente.get(f"CHANGELOG_MODELO_{nome.upper()}", "").strip()
        base = espec.base_url
        if nome == "gemini":
            modelo = modelo or modelo_gemini
            base = base_gemini
        elif nome == "groq":
            modelo = modelo or modelo_groq
            base = base_groq
        cadeia.append(
            Provedor(nome, base, modelo or espec.modelo, chave, espec.max_tokens)
        )
    return cadeia


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--para",
        default="HEAD",
        help="Ref final (tag, branch ou HEAD). Padrão: HEAD.",
    )
    parser.add_argument(
        "--de",
        default=None,
        help="Ref inicial. Padrão: tag mais próxima antes de --para.",
    )
    parser.add_argument(
        "--versao",
        default=None,
        help="Versão exibida no cabeçalho. Padrão: valor de --para.",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Data no formato AAAA-MM-DD. Padrão: data atual.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Arquivo de saída. Sem esta opção, imprime no terminal.",
    )
    parser.add_argument(
        "--resumido",
        action="store_true",
        help="Omite corpo, estatísticas, links e IA: só os títulos dos commits.",
    )
    parser.add_argument(
        "--sem-ia",
        action="store_true",
        help="Não usa IA, mesmo com chaves de provedores definidas.",
    )
    parser.add_argument(
        "--exigir-ia",
        action="store_true",
        help="Falha (código 2) se a IA não puder ser usada, em vez de cair nos títulos.",
    )
    parser.add_argument(
        "--modelo",
        default=os.environ.get("CHANGELOG_MODELO", MODELO_PADRAO),
        help=f"Modelo principal. Padrão: {MODELO_PADRAO} "
        "(ou a variável CHANGELOG_MODELO).",
    )
    parser.add_argument(
        "--modelo-reserva",
        default=os.environ.get("CHANGELOG_MODELO_RESERVA") or MODELO_RESERVA_PADRAO,
        help="Modelo do Groq (o segundo da cadeia padrão); use nenhum para tirar o Groq da cadeia.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CHANGELOG_IA_BASE_URL") or GEMINI_BASE_URL,
    )
    parser.add_argument(
        "--base-url-reserva",
        default=os.environ.get("CHANGELOG_IA_BASE_URL_RESERVA") or GROQ_BASE_URL,
    )
    parser.add_argument(
        "--provedores",
        default=os.environ.get("CHANGELOG_PROVEDORES", ""),
        help="Provedores de IA, em ordem, separados por vírgula "
        f"({', '.join([*PROVEDORES, 'ollama'])}). Padrão: todos os que têm chave, "
        "na ordem dessa lista (ou a variável CHANGELOG_PROVEDORES).",
    )
    parser.add_argument(
        "--limite-prompt",
        type=int,
        default=LIMITE_PROMPT,
        help="Caracteres de diff enviados por requisição à IA. "
        f"Padrão: {LIMITE_PROMPT}.",
    )
    args = parser.parse_args()

    ref_de = args.de or _tag_anterior(args.para)
    if ref_de is None:
        print(
            f"Não achei nenhuma tag antes de '{args.para}'. Use --de explicitamente.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    config: ConfigIA | None = None
    if not (args.sem_ia or args.resumido):
        ordem = _nome_ordem(args.provedores) or [*ORDEM_PADRAO, "ollama"]
        if args.modelo_reserva.strip().lower() == "nenhum":
            ordem = [nome for nome in ordem if nome != "groq"]
        try:
            provedores = _montar_provedores(
                os.environ,
                ordem,
                modelo_gemini=args.modelo.strip() or MODELO_PADRAO,
                modelo_groq=args.modelo_reserva.strip() or MODELO_RESERVA_PADRAO,
                base_gemini=args.base_url,
                base_groq=args.base_url_reserva,
            )
        except ValueError as erro:
            print(f"--provedores: {erro}", file=sys.stderr)
            raise SystemExit(1) from erro
        if provedores:
            primeiro, *demais = provedores
            _log(
                "IA: cadeia de provedores: "
                + " → ".join(f"{p.nome} ({p.modelo})" for p in provedores)
            )
            config = ConfigIA(
                token=primeiro.token,
                modelo=primeiro.modelo,
                limite_prompt=args.limite_prompt,
                base_url=primeiro.base_url,
                max_tokens=primeiro.max_tokens,
                adicionais=tuple(demais),
            )
        else:
            _log("sem chave de IA configurada: usando os títulos dos commits")
    if args.exigir_ia and config is None:
        print(
            "--exigir-ia: a IA está desligada (--sem-ia/--resumido) ou falta a chave.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        changelog = gerar_changelog(
            ref_de=ref_de,
            ref_para=args.para,
            detalhado=not args.resumido,
            versao=args.versao,
            data_lancamento=args.data,
            ia=config,
            exigir_ia=args.exigir_ia,
        )
    except ErroIA as erro:
        print(f"--exigir-ia: a IA falhou: {erro}", file=sys.stderr)
        raise SystemExit(2) from erro

    if args.output:
        args.output.write_text(changelog, encoding="utf-8")
    else:
        print(changelog, end="")


if __name__ == "__main__":
    main()
