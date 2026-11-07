# ==============================================================================
# config_cmd.py
# ------------------------------------------------------------------------------
# Comando `qobuz-dl config [ação]`: consulta e edita o config.ini sem abrir um
# editor. Reúne o que antes eram flags soltas (`-sc/--show-config`, `-r/--reset`).
#
#   show   (padrão) mostra caminhos e opções, com as credenciais ocultas
#   path   imprime só o caminho do config.ini (bom para scripts)
#   get    CHAVE        imprime o valor de uma opção
#   set    CHAVE VALOR  altera uma opção existente (ou cria, com --nova)
#   reset  recria o arquivo (feito em cli.py, que tem o assistente de login)
#
# A edição é feita LINHA A LINHA, não com `configparser.write`, para não perder
# os comentários do arquivo. Credenciais (e-mail, senha, tokens, secrets) nunca
# são impressas nem aceitas em `set`: use `qobuz-dl auth -l`.
#
# ONDE PROCURAR quando precisar mexer em algo:
#   - formato da tela                   -> `_mostrar`
#   - regras de validação do `set`      -> `validar_valor`
#   - edição preservando comentários    -> `definir_chave`
# ==============================================================================

from __future__ import annotations

import configparser
import os
import re
import tempfile
from typing import Any, Optional

from qobuz_dl import ui

ACOES = ("show", "path", "get", "set", "reset")
OCULTO = "<redacted>"

_BOOLEANOS = {"true", "false", "1", "0", "yes", "no", "on", "off"}
_SECAO = re.compile(r"^\s*\[(?P<nome>[^\]]+)\]\s*$")
_CHAVE = re.compile(
    r"^(?P<recuo>[ \t]*)(?P<chave>[^\s=:#;][^=:]*?)(?P<sep>\s*[=:]\s*)(?P<valor>.*)$"
)


class ConfigErro(Exception):
    """Pedido inválido (chave desconhecida, valor do tipo errado...)."""


def ler_config(caminho: str) -> configparser.ConfigParser:
    """Lê o INI sem interpolação (formatos de pasta usam chaves e `%`)."""
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(caminho, encoding="utf-8")
    return cfg


def secao_padrao(cfg: configparser.ConfigParser) -> str:
    """Seção usada pelo programa: `[qobuz]` quando existe, senão `[DEFAULT]`."""
    return "qobuz" if cfg.has_section("qobuz") else "DEFAULT"


def _valor_visivel(chave: str, valor: str, sensiveis) -> str:
    return OCULTO if chave.lower() in sensiveis and valor else valor


def validar_valor(antigo: Optional[str], novo: str) -> None:
    """Confere se ``novo`` combina com o tipo do valor que já estava no arquivo.

    Se o antigo era booleano (`true`/`false`...), o novo também tem de ser; se
    era número inteiro, o novo tem de ser inteiro. Texto aceita qualquer coisa.
    Sem valor antigo (chave nova) não há o que comparar.
    """
    if antigo is None or antigo == "":
        return
    if antigo.strip().lower() in _BOOLEANOS:
        if novo.strip().lower() not in _BOOLEANOS:
            raise ConfigErro(
                f"o valor atual ({antigo}) é verdadeiro/falso; use true ou false."
            )
    elif re.fullmatch(r"-?\d+", antigo.strip()):
        if not re.fullmatch(r"-?\d+", novo.strip()):
            raise ConfigErro(f"o valor atual ({antigo}) é um número inteiro.")


def definir_chave(
    texto: str, secao: str, chave: str, valor: str, *, criar: bool = False
) -> tuple[str, Optional[str]]:
    """Troca (ou cria) ``chave`` na ``secao`` do texto do INI, sem mexer no resto.

    Devolve ``(novo_texto, valor_antigo)``; ``valor_antigo`` é ``None`` quando a
    chave não existia. Comentários, linhas em branco e a ordem são preservados;
    se o valor antigo continuava em linhas recuadas, essas linhas somem junto.
    Levanta ``ConfigErro`` se a seção não existe ou, sem ``criar``, se a chave
    não existe.
    """
    linhas = texto.splitlines()
    alvo = secao.lower()
    dentro = False
    achou_secao = False
    ultima_da_secao = None  # índice da última linha com conteúdo da seção
    for i, linha in enumerate(linhas):
        m = _SECAO.match(linha)
        if m:
            dentro = m.group("nome").strip().lower() == alvo
            achou_secao = achou_secao or dentro
            if dentro:
                ultima_da_secao = i
            continue
        if not dentro:
            continue
        if linha.strip():
            ultima_da_secao = i
        c = _CHAVE.match(linha)
        if c and c.group("chave").strip().lower() == chave.lower():
            antigo = c.group("valor").rstrip()
            fim = i + 1
            while (
                fim < len(linhas)
                and linhas[fim][:1] in (" ", "\t")
                and linhas[fim].strip()
            ):
                fim += 1  # continuação do valor antigo
            linhas[i:fim] = [
                f"{c.group('recuo')}{c.group('chave')}{c.group('sep')}{valor}"
            ]
            return _juntar(linhas, texto), antigo
    if not achou_secao:
        raise ConfigErro(f"a seção [{secao}] não existe no config.ini.")
    if not criar:
        raise ConfigErro(
            f"a opção '{chave}' não existe na seção [{secao}]. Use --nova para criá-la."
        )
    if ultima_da_secao is None:
        raise ConfigErro(
            f"não foi possível localizar a posicão da seção [{secao}] no config.ini."
        )
    linhas.insert(ultima_da_secao + 1, f"{chave} = {valor}")
    return _juntar(linhas, texto), None


def _juntar(linhas: list[str], original: str) -> str:
    fim = "\n" if original.endswith("\n") else ""
    return "\n".join(linhas) + fim


def _gravar_texto(caminho: str, texto: str) -> None:
    """Grava atomicamente e deixa o arquivo legível só pelo dono (0600)."""
    pasta = os.path.dirname(os.path.abspath(caminho))
    fd, temporario = tempfile.mkstemp(prefix=".config.", dir=pasta, text=True)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as saida:
            saida.write(texto)
            saida.flush()
            os.fsync(saida.fileno())
        os.replace(temporario, caminho)
        if os.name == "posix":
            os.chmod(caminho, 0o600)
    except BaseException:
        try:
            os.unlink(temporario)
        except FileNotFoundError:
            # Limpeza best-effort: o temporário pode já ter sumido.
            pass
        raise


def dados_visiveis(
    cfg: configparser.ConfigParser, sensiveis
) -> dict[str, dict[str, str]]:
    """``{seção: {chave: valor}}`` com as credenciais trocadas por ``<redacted>``."""
    saida: dict[str, dict[str, str]] = {}
    if cfg.defaults():
        saida["DEFAULT"] = {
            k: _valor_visivel(k, v, sensiveis) for k, v in cfg.defaults().items()
        }
    for secao in cfg.sections():
        saida[secao] = {
            k: _valor_visivel(k, v, sensiveis)
            for k, v in cfg.items(secao)
            if k not in cfg.defaults() or cfg.has_option(secao, k)
        }
    return saida


def estado_do_arquivo(config_file: str, cfg: configparser.ConfigParser) -> str:
    """``"ausente"``, ``"vazio"`` (existe, sem nenhuma opção) ou ``"ok"``."""
    if not os.path.isfile(config_file):
        return "ausente"
    if not cfg.sections() and not cfg.defaults():
        return "vazio"
    return "ok"


_AVISO_ESTADO = {
    "ausente": "O config.ini não existe neste caminho. Crie-o com: qobuz-dl config reset",
    "vazio": "O config.ini existe, mas está vazio (sem nenhuma opção). Refaça com: qobuz-dl config reset",
}


def _mostrar(cfg, config_file, db_path, library_db, sensiveis, como_json) -> int:
    dados = dados_visiveis(cfg, sensiveis)
    estado = estado_do_arquivo(config_file, cfg)
    if como_json:
        ui.emit_json(
            {
                "arquivo": config_file,
                "estado": estado,
                "banco_de_downloads": db_path,
                "catalogo": library_db,
                "opcoes": dados,
            }
        )
        return 0 if estado == "ok" else 1
    caminhos = ui.Painel("⚙️  CONFIGURAÇÃO")
    caminhos.kv("Arquivo", config_file)
    caminhos.kv("Banco de downloads", db_path)
    if library_db:
        caminhos.kv("Catálogo", library_db)
    if estado != "ok":
        caminhos.rule()
        caminhos.warn(_AVISO_ESTADO[estado])
    caminhos.emit()
    if estado != "ok":
        return 1
    for secao, opcoes in dados.items():
        painel = ui.Painel(f"[{secao}]")
        for chave, valor in opcoes.items():
            painel.kv(chave, valor if valor != "" else "(vazio)")
        if not opcoes:
            painel.detail("(sem opções)")
        painel.emit(lead=False)
    return 0


def cmd_config(
    args: Any,
    *,
    config_file: str,
    db_path: str,
    library_db: str = "",
    sensiveis=frozenset(),
) -> int:
    """Executa ``config show|path|get|set``; devolve o código de saída."""
    acao = getattr(args, "action", None) or "show"
    if acao == "path":
        ui.emit_always(config_file)
        return 0

    cfg = ler_config(config_file)
    if acao == "show":
        return _mostrar(
            cfg,
            config_file,
            db_path,
            library_db,
            sensiveis,
            bool(getattr(args, "json", False)),
        )

    estado = estado_do_arquivo(config_file, cfg)
    if estado != "ok":
        ui.error(_AVISO_ESTADO[estado])
        return 1

    chave = getattr(args, "key", None)
    if not chave:
        ui.error(f"Informe a opção: qobuz-dl config {acao} CHAVE")
        return 2
    secao = secao_padrao(cfg)

    if acao == "get":
        if not cfg.has_option(secao, chave):
            ui.error(f"A opção '{chave}' não existe na seção [{secao}].")
            return 1
        ui.emit_always(_valor_visivel(chave, cfg.get(secao, chave), sensiveis))
        return 0

    if acao == "set":
        valor = getattr(args, "value", None)
        if valor is None:
            ui.error("Informe o valor: qobuz-dl config set CHAVE VALOR")
            return 2
        if chave.lower() in sensiveis:
            ui.error(
                f"'{chave}' é uma credencial e não é alterada por aqui "
                "(ela iria para o histórico do terminal). Use: qobuz-dl auth -l"
            )
            return 1
        try:
            antigo_ini = cfg.get(secao, chave) if cfg.has_option(secao, chave) else None
            validar_valor(antigo_ini, valor)
            with open(config_file, encoding="utf-8") as arquivo:
                texto = arquivo.read()
            novo_texto, antigo = definir_chave(
                texto, secao, chave, valor, criar=bool(getattr(args, "nova", False))
            )
        except ConfigErro as erro:
            ui.error(f"Não alterei nada: {erro}")
            return 1
        except OSError as erro:
            ui.error(f"Não consegui ler o config.ini: {erro}")
            return 1
        try:
            _gravar_texto(config_file, novo_texto)
        except OSError as erro:
            ui.error(f"Não consegui gravar o config.ini: {erro}")
            return 1
        antes = "(nova)" if antigo is None else (antigo or "(vazio)")
        ui.Painel("✏️  OPÇÃO ALTERADA").kv("Opção", f"[{secao}] {chave}").kv(
            "Antes", antes
        ).kv("Agora", valor).emit()
        return 0

    ui.error(f"Ação desconhecida: {acao}")
    return 2
