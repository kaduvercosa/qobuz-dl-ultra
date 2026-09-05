"""
Gera o relatorio de downloads (album/faixa/playlist).

.report.json (arquivo oculto, prefixo com ponto) e' a fonte de verdade
-- lido e reescrito a cada faixa que termina, sob lock por pasta, pra
suportar downloads em paralelo sem duplicar/perder entradas. Fica
escondido de proposito: ninguem deveria abrir esse arquivo na mao,
so' existe pra alimentar o report.html. report.html e' gerado
automaticamente junto (ver _save_report), como a "vitrine" visivel pra
abrir no navegador -- nunca e' lido de volta, entao uma falha ao
gera-lo nunca derruba o download em si (so' loga e segue).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from qobuz_dl.report_viewer import renderizar_html as _renderizar_report_html
except ImportError as e:
    # ANTES: falha silenciosa (_renderizar_report_html virava None sem
    # nenhum log, em lugar nenhum). Se report_viewer.py nao estivesse no
    # lugar certo dentro do pacote (qobuz_dl/report_viewer.py) ou o pacote
    # nao tivesse sido reinstalado apos adiciona-lo, o report.html
    # simplesmente nunca era gerado e nao havia pista nenhuma do motivo.
    # Agora loga uma vez, na inicializacao, pra o problema ficar visivel.
    _renderizar_report_html = None
    logger.warning(
        "report.html desativado: nao foi possivel importar "
        "qobuz_dl.report_viewer (%s). Verifique se report_viewer.py esta "
        "em qobuz_dl/report_viewer.py e se o pacote foi reinstalado.",
        e,
    )

REPORT_FILENAME = ".report.json"
_BRT = timezone(timedelta(hours=-3), name="BRT")

_locks: Dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


# ============================================================================
# INFRAESTRUTURA
# ============================================================================


async def _get_lock(path: str) -> asyncio.Lock:
    """Retorna um lock por arquivo para impedir gravacoes concorrentes."""
    async with _locks_guard:
        lock = _locks.get(path)
        if lock is None:
            lock = asyncio.Lock()
            _locks[path] = lock
        return lock


def _now_iso() -> str:
    """Retorna data/hora atual em BRT no formato ISO 8601."""
    return datetime.now(_BRT).isoformat(timespec="seconds")


def _load_report(path: str) -> dict:
    """Le o JSON existente ou retorna um dicionario vazio."""
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Relatorio ilegivel em %s; sera recriado (%s)", path, exc)
        return {}


def _atomic_write_json(path: str, data: Any) -> None:
    """Grava JSON de forma atomica."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(tmp_path, path)


def _norm_id(value: Any) -> Optional[str]:
    """Converte ID em texto normalizado."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _formatar_artistas(valor: Any) -> str:
    """Converte artista individual ou lista em texto separado por virgulas."""
    if valor is None:
        return ""
    if isinstance(valor, (list, tuple, set)):
        return ", ".join(str(item).strip() for item in valor if str(item).strip())
    return str(valor).strip()


def _lista_artistas(valor: Any) -> List[str]:
    """Converte artista individual, lista ou texto CSV em lista limpa."""
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        return [str(item).strip() for item in valor if str(item).strip()]

    texto = str(valor).strip()
    if not texto:
        return []
    return [item.strip() for item in texto.split(",") if item.strip()]


def _normalizar_status(status: str) -> str:
    """Normaliza o status legado ok para concluido."""
    return "concluido" if status == "ok" else (status or "pendente")


def _track_sort_key(entry: dict) -> tuple:
    """Ordena faixas por disco e numero."""
    numero = entry.get("numero")
    try:
        return (0, int(numero))
    except (TypeError, ValueError):
        return (1, str(numero or ""))


# ============================================================================
# ORDEM DE SERIALIZACAO
# ============================================================================

_REPORT_FIELD_ORDER = [
    "tipo",
    "identificacao",
    "qualidade",
    "progresso",
    "extra",
    "faixas",
]

_IDENTIFICACAO_ITEM_ORDER = ["titulo", "artista", "tipo_lancamento", "id", "upc", "url"]

_QUALIDADE_ITEM_ORDER = ["formato", "bit_depth", "sampling_rate", "alvo_atingida"]

_PROGRESSO_ITEM_ORDER = ["estado", "resumo"]

_ESTADO_ITEM_ORDER = ["situacao", "verificado", "criado_em", "atualizado_em"]

_RESUMO_ORDER = ["total", "concluidas", "puladas", "falhas", "pendentes"]

_FAIXA_FIELD_ORDER = ["numero", "id", "identificacao", "download", "letras"]

_IDENTIFICACAO_FAIXA_ORDER = [
    "titulo",
    "main_artists",
    "artista",
    "artista_album",
    "album",
    "tipo_lancamento",
    "isrc",
    "compositor",
]

_DOWNLOAD_FAIXA_ORDER = ["situacao", "motivo", "checksum", "atualizado_em"]

_LETRAS_FAIXA_ORDER = [
    "situacao",
    "sincronizada",
    "bilingue",
    "idioma_original",
    "traducao_disponivel",
    "fonte",
    "destino",
    "observacao",
]

_TIPO_PRIORIDADE = {"faixa": 1, "playlist": 2, "album": 3}


def _ordenar(dados: dict, ordem: List[str]) -> dict:
    """Reordena chaves conhecidas, mantendo chaves extras ao final."""
    saida = {chave: dados[chave] for chave in ordem if chave in dados}
    for chave, valor in dados.items():
        if chave not in saida:
            saida[chave] = valor
    return saida


# ============================================================================
# ESTRUTURAS DE RELATORIO
# ============================================================================


def _skeleton(
    tipo: str,
    titulo: str,
    artista: Any,
    item_id: str,
    extra: Optional[dict],
    qualidade: Optional[dict],
    tipo_lancamento: Optional[str] = None,
    upc: Optional[str] = None,
) -> dict:
    """Cria o esqueleto de um relatorio no nivel do item."""
    agora = _now_iso()
    qualidade = qualidade or {}
    extra = extra or {}

    return {
        "tipo": tipo,
        "identificacao": {
            "titulo": titulo or "",
            "artista": _formatar_artistas(artista),
            "tipo_lancamento": tipo_lancamento or "",
            "id": item_id or "",
            "upc": upc or extra.get("upc", ""),
            "url": extra.get("url", ""),
        },
        "qualidade": {
            "formato": qualidade.get("formato", ""),
            "bit_depth": qualidade.get("bit_depth"),
            "sampling_rate": qualidade.get("sampling_rate"),
            "alvo_atingida": None,
        },
        "progresso": {
            "estado": {
                "situacao": "em_andamento",
                "verificado": False,
                "criado_em": agora,
                "atualizado_em": agora,
            },
            "resumo": {},
        },
        "extra": extra,
        "faixas": [],
    }


def _criar_faixa_pendente(faixa: dict) -> dict:
    """Cria uma faixa prevista ainda sem resultado do download."""
    artistas = _lista_artistas(faixa.get("artista", ""))
    artista_album = _formatar_artistas(faixa.get("artista_album", ""))
    album = _formatar_artistas(faixa.get("album", ""))

    return {
        "numero": faixa.get("numero"),
        "id": faixa.get("id"),
        "identificacao": {
            "titulo": faixa.get("titulo", "Faixa"),
            "main_artists": artistas,
            "artista": ", ".join(artistas),
            "artista_album": artista_album,
            "album": album,
            "tipo_lancamento": faixa.get("tipo_lancamento", ""),
            "isrc": faixa.get("isrc", ""),
            "compositor": _formatar_artistas(faixa.get("compositor", "")),
        },
        "download": {
            "situacao": "pendente",
            "motivo": "",
            "checksum": None,
            "atualizado_em": None,
        },
        "letras": {},
    }


def _garantir_estrutura_item(report: dict) -> None:
    """Garante campos obrigatorios do relatorio no nivel do item."""
    report.setdefault("tipo", "faixa")
    report.setdefault("identificacao", {})
    report.setdefault("qualidade", {})
    report.setdefault("extra", {})
    report.setdefault("faixas", [])

    identificacao = report["identificacao"]
    for chave in _IDENTIFICACAO_ITEM_ORDER:
        identificacao.setdefault(chave, "")

    qualidade = report["qualidade"]
    qualidade.setdefault("formato", "")
    qualidade.setdefault("bit_depth", None)
    qualidade.setdefault("sampling_rate", None)
    qualidade.setdefault("alvo_atingida", None)

    progresso = report.setdefault("progresso", {})
    estado = progresso.setdefault("estado", {})
    estado.setdefault("situacao", "em_andamento")
    estado.setdefault("verificado", False)
    estado.setdefault("criado_em", _now_iso())
    estado.setdefault("atualizado_em", _now_iso())
    progresso.setdefault("resumo", {})


def _garantir_estrutura_faixa(faixa: dict) -> None:
    """Garante campos obrigatorios do relatorio no nivel de faixa."""
    identificacao = faixa.setdefault("identificacao", {})
    identificacao.setdefault("titulo", "Faixa")
    identificacao.setdefault("main_artists", [])
    identificacao.setdefault("artista", "")
    identificacao.setdefault("artista_album", "")
    identificacao.setdefault("album", "")
    identificacao.setdefault("tipo_lancamento", "")
    identificacao.setdefault("isrc", "")
    identificacao.setdefault("compositor", "")

    download = faixa.setdefault("download", {})
    download.setdefault("situacao", "pendente")
    download.setdefault("motivo", "")
    download.setdefault("checksum", None)
    download.setdefault("atualizado_em", None)

    faixa.setdefault("letras", {})


# ============================================================================
# API PUBLICA USADA PELO DOWNLOADER
# ============================================================================


async def init_report(
    dirn: str,
    tipo: str,
    titulo: str,
    artista: Any = "",
    tipo_lancamento: str = "",
    item_id: str = "",
    extra: Optional[dict] = None,
    qualidade: Optional[dict] = None,
    faixas_previstas: Optional[List[dict]] = None,
    upc: Optional[str] = None,
    filename: str = REPORT_FILENAME,
) -> str:
    """Cria ou atualiza o relatorio do item e registra as faixas previstas."""
    path = os.path.join(dirn, filename)
    lock = await _get_lock(path)

    async with lock:
        report = _load_report(path)

        if not report:
            report = _skeleton(
                tipo=tipo,
                titulo=titulo,
                artista=artista,
                item_id=item_id,
                extra=extra,
                qualidade=qualidade,
                tipo_lancamento=tipo_lancamento,
                upc=upc,
            )
        else:
            _garantir_estrutura_item(report)
            promovendo = _TIPO_PRIORIDADE.get(tipo, 0) > _TIPO_PRIORIDADE.get(
                report.get("tipo", ""), 0
            )
            if promovendo:
                report["tipo"] = tipo

                identificacao = report["identificacao"]
                identificacao["titulo"] = titulo
                identificacao["artista"] = _formatar_artistas(artista)
                identificacao["tipo_lancamento"] = tipo_lancamento or ""
                identificacao["id"] = item_id
                if upc:
                    identificacao["upc"] = upc

                report["extra"] = extra or {}
                if extra and extra.get("upc") and not identificacao.get("upc"):
                    identificacao["upc"] = extra["upc"]
                if extra and extra.get("url"):
                    identificacao["url"] = extra["url"]

                if qualidade:
                    qualidade_item = report["qualidade"]
                    for chave in ("formato", "bit_depth", "sampling_rate"):
                        if chave in qualidade and qualidade[chave] is not None:
                            qualidade_item[chave] = qualidade[chave]
            # tipo igual ou "menor": cabecalho existente e mantido como esta
            # (ex.: retomando um album interrompido -- nao regenera o
            # cabecalho a cada retry, so completa faixas que faltam).

        _garantir_estrutura_item(report)

        if faixas_previstas:
            existentes = {_norm_id(faixa.get("id")) for faixa in report["faixas"]}
            for faixa in faixas_previstas:
                faixa_id = _norm_id(faixa.get("id"))
                if faixa_id in existentes:
                    continue
                report["faixas"].append(_criar_faixa_pendente(faixa))
                existentes.add(faixa_id)
            report["faixas"].sort(key=_track_sort_key)

        _atualizar_progresso_item(report)
        _recalc_cabecalho_dinamico(report)
        _recalc_resumo(report)
        _save_report(path, report)
        return path


async def update_track_status(
    dirn: str,
    numero: Any,
    item_id: Any,
    titulo: str,
    status: str,
    artista: Any = "",
    artista_album: Any = None,
    tipo_lancamento: Optional[str] = None,
    motivo: str = "",
    isrc: str = "",
    compositor: Any = "",
    checksum: Optional[str] = None,
    letras: Optional[dict] = None,
    tipo_default: str = "faixa",
    titulo_default: Optional[str] = None,
    id_default: str = "",
    filename: str = REPORT_FILENAME,
    **_ignored: Any,
) -> None:
    """
    Atualiza uma faixa no relatorio.

    A assinatura aceita os argumentos exatamente usados pelo downloader.py,
    incluindo artista_album, tipo_default, titulo_default e id_default.
    Argumentos extras sao ignorados intencionalmente para manter compatibilidade
    com futuras versoes do downloader.

    Assim como `init_report`, so promove o cabecalho (tipo/titulo/id) quando
    `tipo_default` tiver prioridade MAIOR que o tipo ja registrado (ex.:
    faixa avulsa -> playlist na mesma pasta). Titulo/id NAO sao mais
    sobrescritos incondicionalmente a cada chamada -- so quando a pasta e
    criada agora ou quando esta chamada de fato promove o tipo, senao um
    cabecalho ja promovido (playlist) regride pro titulo/id de uma chamada
    de prioridade MENOR que chegue depois (faixa avulsa do mesmo release).
    Artista e tipo_lancamento do cabecalho nunca sao setados aqui -- sempre
    recalculados a partir de TODAS as faixas por `_recalc_cabecalho_dinamico`.
    """
    path = os.path.join(dirn, filename)
    lock = await _get_lock(path)

    async with lock:
        report = _load_report(path)
        is_new = not report
        if is_new:
            report = _skeleton(
                tipo=tipo_default,
                titulo=titulo_default or titulo,
                artista=artista_album or artista,
                item_id=id_default,
                extra=None,
                qualidade=None,
                tipo_lancamento=tipo_lancamento,
            )
            promovendo = False
        else:
            _garantir_estrutura_item(report)
            promovendo = _TIPO_PRIORIDADE.get(tipo_default, 0) > _TIPO_PRIORIDADE.get(
                report.get("tipo", ""), 0
            )
            if promovendo:
                report["tipo"] = tipo_default

        _garantir_estrutura_item(report)
        item_identificacao = report["identificacao"]

        if is_new or promovendo:
            if titulo_default:
                item_identificacao["titulo"] = titulo_default
            if id_default:
                item_identificacao["id"] = id_default
        # tipo igual ou "menor": titulo/id do cabecalho mantidos como estao
        # -- so as faixas e o recalculo dinamico (artista/tipo_lancamento)
        # continuam de qualquer forma, mais abaixo.

        alvo = _norm_id(item_id)
        entrada = next(
            (faixa for faixa in report["faixas"] if _norm_id(faixa.get("id")) == alvo),
            None,
        )

        if entrada is None:
            entrada = _criar_faixa_pendente(
                {
                    "numero": numero,
                    "id": item_id,
                    "titulo": titulo,
                    "artista": artista,
                    "artista_album": artista_album or "",
                    "album": titulo_default if tipo_default == "faixa" else "",
                    "tipo_lancamento": tipo_lancamento or "",
                    "isrc": isrc,
                    "compositor": compositor,
                }
            )
            report["faixas"].append(entrada)

        _garantir_estrutura_faixa(entrada)
        entrada["numero"] = numero if numero is not None else entrada.get("numero")

        identificacao = entrada["identificacao"]
        identificacao["titulo"] = titulo or identificacao.get("titulo", "Faixa")

        if artista:
            artistas = _lista_artistas(artista)
            identificacao["main_artists"] = artistas
            identificacao["artista"] = ", ".join(artistas)

        if artista_album is not None:
            identificacao["artista_album"] = _formatar_artistas(artista_album)

        # Para faixa avulsa, titulo_default e o album. Para playlist ele e a playlist,
        # entao nao deve ser repetido como album da faixa.
        if tipo_default == "faixa" and titulo_default:
            identificacao["album"] = titulo_default

        if tipo_lancamento is not None:
            identificacao["tipo_lancamento"] = tipo_lancamento
        if isrc:
            identificacao["isrc"] = isrc
        if compositor:
            identificacao["compositor"] = _formatar_artistas(compositor)

        download = entrada["download"]
        download["situacao"] = _normalizar_status(status)
        download["motivo"] = motivo or ""
        if checksum is not None:
            download["checksum"] = checksum
        download["atualizado_em"] = _now_iso()

        if letras:
            entrada["letras"] = letras

        report["faixas"].sort(key=_track_sort_key)
        _atualizar_progresso_item(report)
        _recalc_cabecalho_dinamico(report)
        _recalc_resumo(report)
        _save_report(path, report)


async def finalize_report(
    dirn: str,
    completo: bool,
    qualidade_atingida: Optional[bool] = None,
    verificado: bool = False,
    filename: str = REPORT_FILENAME,
) -> None:
    """Finaliza o estado geral de um relatorio."""
    path = os.path.join(dirn, filename)
    lock = await _get_lock(path)

    async with lock:
        report = _load_report(path)
        if not report:
            return

        _garantir_estrutura_item(report)
        estado = report["progresso"]["estado"]
        estado["situacao"] = "completo" if completo else "incompleto"
        estado["verificado"] = verificado
        estado["atualizado_em"] = _now_iso()

        if qualidade_atingida is not None:
            report["qualidade"]["alvo_atingida"] = qualidade_atingida

        _recalc_resumo(report)
        _save_report(path, report)


# ============================================================================
# CALCULOS E APRESENTACAO
# ============================================================================


def _atualizar_progresso_item(report: dict) -> None:
    _garantir_estrutura_item(report)
    report["progresso"]["estado"]["atualizado_em"] = _now_iso()


def _recalc_resumo(report: dict) -> None:
    """Recalcula o resumo com base no status de cada faixa."""
    _garantir_estrutura_item(report)

    def tem_status(faixa: dict, status: str) -> bool:
        return faixa.get("download", {}).get("situacao") == status

    faixas = report.get("faixas", [])
    report["progresso"]["resumo"] = {
        "total": len(faixas),
        "concluidas": sum(1 for faixa in faixas if tem_status(faixa, "concluido")),
        "puladas": sum(1 for faixa in faixas if tem_status(faixa, "pulada")),
        "falhas": sum(1 for faixa in faixas if tem_status(faixa, "falha")),
        "pendentes": sum(1 for faixa in faixas if tem_status(faixa, "pendente")),
    }


def _recalc_cabecalho_dinamico(report: dict) -> None:
    """
    Recalcula "artista" e "tipo_lancamento" do cabecalho a partir de TODAS
    as faixas ja registradas -- roda de novo a cada faixa que chega, entao
    converge pro valor certo independente de qual faixa "nasceu" o arquivo
    primeiro (em playlist/lote os downloads rodam em paralelo, entao isso e
    meio aleatorio). Roda por completo a cada chamada (nao so quando o
    campo ainda esta vazio), senao uma faixa de artista/release diferente
    chegando DEPOIS da primeira nunca faria o cabecalho reconvergir.

    Artista: usa o artista_album de cada faixa (o OFICIAL do release dela),
    caindo pro artista da faixa isolada so como fallback -- evita falso
    positivo de "Vários Artistas" quando faixas do MESMO release tem
    performer divergente so por causa de um "feat." pontual. Se houver mais
    de um artista distinto entre as faixas, vira "Vários Artistas".

    Tipo de lancamento: usa o tipo_lancamento de cada faixa (Single/EP/
    Album/Live/Compilação do release AO QUAL ELA PERTENCE -- nao de quantas
    faixas estao sendo baixadas nesta sessao). Se todas concordam, usa esse
    valor; se vierem de releases com classificacoes diferentes (lote/
    playlist misto), vira "Diversos".

    Aplica pra QUALQUER tipo que nao seja "album" -- faixa avulsa, lote
    (que tambem usa tipo="faixa") e playlist passam TODOS por aqui, sem
    excecao. Album fica de fora: ali e um release so, o cabecalho ja vem
    definitivo do proprio init_report e nao deve ser recalculado.
    """
    if report.get("tipo") == "album":
        return

    identificacao = report["identificacao"]
    faixas = report.get("faixas", [])

    artistas = {
        (
            f.get("identificacao", {}).get("artista_album")
            or f.get("identificacao", {}).get("artista")
        )
        for f in faixas
        if (
            f.get("identificacao", {}).get("artista_album")
            or f.get("identificacao", {}).get("artista")
        )
    }
    if len(artistas) == 1:
        identificacao["artista"] = next(iter(artistas))
    elif len(artistas) > 1:
        identificacao["artista"] = "Vários Artistas"

    tipos = {
        f.get("identificacao", {}).get("tipo_lancamento")
        for f in faixas
        if f.get("identificacao", {}).get("tipo_lancamento")
    }
    if len(tipos) == 1:
        identificacao["tipo_lancamento"] = next(iter(tipos))
    elif len(tipos) > 1:
        identificacao["tipo_lancamento"] = "Diversos"


def _organizar_report(report: dict) -> dict:
    """Organiza a ordem dos campos antes de salvar o JSON."""
    organizado = _ordenar(report, _REPORT_FIELD_ORDER)

    if isinstance(organizado.get("identificacao"), dict):
        organizado["identificacao"] = _ordenar(
            organizado["identificacao"], _IDENTIFICACAO_ITEM_ORDER
        )

    if isinstance(organizado.get("qualidade"), dict):
        organizado["qualidade"] = _ordenar(
            organizado["qualidade"], _QUALIDADE_ITEM_ORDER
        )

    progresso = organizado.get("progresso")
    if isinstance(progresso, dict):
        progresso = _ordenar(progresso, _PROGRESSO_ITEM_ORDER)
        if isinstance(progresso.get("estado"), dict):
            progresso["estado"] = _ordenar(progresso["estado"], _ESTADO_ITEM_ORDER)
        if isinstance(progresso.get("resumo"), dict):
            progresso["resumo"] = _ordenar(progresso["resumo"], _RESUMO_ORDER)
        organizado["progresso"] = progresso

    faixas = organizado.get("faixas")
    if isinstance(faixas, list):
        faixas_organizadas = []
        for faixa in faixas:
            faixa_organizada = _ordenar(faixa, _FAIXA_FIELD_ORDER)
            if isinstance(faixa_organizada.get("identificacao"), dict):
                faixa_organizada["identificacao"] = _ordenar(
                    faixa_organizada["identificacao"], _IDENTIFICACAO_FAIXA_ORDER
                )
            if isinstance(faixa_organizada.get("download"), dict):
                faixa_organizada["download"] = _ordenar(
                    faixa_organizada["download"], _DOWNLOAD_FAIXA_ORDER
                )
            if isinstance(faixa_organizada.get("letras"), dict):
                faixa_organizada["letras"] = _ordenar(
                    faixa_organizada["letras"], _LETRAS_FAIXA_ORDER
                )
            faixas_organizadas.append(faixa_organizada)
        organizado["faixas"] = faixas_organizadas

    return organizado


def _save_report(path: str, report: dict) -> None:
    """
    Escreve .report.json (fonte de verdade oculta, reorganizada numa
    ordem de campos fixa -- ver `_organizar_report`) e, em seguida,
    regenera o report.html correspondente na MESMA pasta -- sempre
    juntos, nessa ordem, e sempre sob o mesmo lock por pasta que ja
    protege o json (ver `_get_lock`/chamadores), entao os dois nunca
    ficam dessincronizados mesmo com faixas terminando em paralelo.

    O html e' visivel de proposito (e' a "vitrine"); o json fica oculto
    e nunca e' lido de volta -- se a geracao do html falhar por
    qualquer motivo, so' loga e segue, nunca derruba o download por
    causa disso.
    """
    organizado = _organizar_report(report)
    _atomic_write_json(path, organizado)

    if _renderizar_report_html is not None:
        try:
            html_path = os.path.join(os.path.dirname(path), "report.html")
            tmp_path = f"{html_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(_renderizar_report_html(organizado))
            os.replace(tmp_path, html_path)
        except Exception as e:
            logger.warning("Falha ao gerar report.html em %s (%s)", path, e)


# ============================================================================
# RELATORIOS DA COLECAO
# ============================================================================


def generate_index_entry(
    db_path: str,
    album_id: str,
    album_title: str,
    artist_name: str,
    dirn: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    release_date: str,
    url: str,
) -> None:
    """Adiciona um album ao indice JSONL da colecao."""
    entry = {
        "album_id": album_id,
        "album_titulo": album_title,
        "artista": artist_name,
        "caminho": os.path.abspath(dirn),
        "formato": file_format,
        "bit_depth": bit_depth,
        "sampling_rate": sampling_rate,
        "data_lancamento": release_date,
        "url": url,
        "gerado_em": _now_iso(),
    }
    index_path = os.path.join(os.path.dirname(db_path), "collection_index.jsonl")
    with open(index_path, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_collection_report(db_path: str, stats: dict) -> None:
    """Acrescenta um resumo Markdown da colecao."""
    report_path = os.path.join(os.path.dirname(db_path), "collection_report.md")
    linhas = [
        "# Relatorio da Colecao",
        "",
        f"Gerado em: {_now_iso()}",
        "",
        "## Totais",
        f"- Albuns: {stats.get('albums', 0)}",
        f"- Faixas: {stats.get('tracks', 0)}",
        f"- Hi-Res (>=24bit): {stats.get('hires', 0)}",
        f"- FLAC: {stats.get('flac', 0)}",
        f"- MP3: {stats.get('mp3', 0)}",
        f"- Qualidade atingida: {stats.get('quality_met', 0)}",
        f"- Qualidade nao atingida: {stats.get('quality_not_met', 0)}",
    ]

    for titulo, chave in (
        ("Formatos", "formats"),
        ("Bit Depths", "bit_depths"),
        ("Sample Rates", "sample_rates"),
    ):
        linhas.extend(["", f"## {titulo}"])
        for nome, total in stats.get(chave, {}).items():
            linhas.append(f"- {nome}: {total}")

    linhas.extend(["", "## Top Artistas"])
    for nome, total in stats.get("top_artists", [])[:10]:
        linhas.append(f"- {nome}: {total}")

    if stats.get("oldest") or stats.get("newest"):
        linhas.extend(["", "## Periodo"])
        if stats.get("oldest"):
            linhas.append(f"- Mais antigo: {stats['oldest']}")
        if stats.get("newest"):
            linhas.append(f"- Mais recente: {stats['newest']}")

    with open(report_path, "a", encoding="utf-8") as file:
        file.write("\n".join(linhas) + "\n")
