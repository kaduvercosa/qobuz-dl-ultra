"""Reaproveita uma faixa já baixada em OUTRA pasta, SÓ depois de conferir tudo.

Caso típico: a faixa veio por uma playlist (pasta própria) e depois o álbum
completo é baixado. Em vez de baixar de novo, o arquivo existente é COPIADO para
o temporário do pipeline; o resto (tags do álbum, letras, nome final, limpeza da
cópia antiga) roda igual a um download novo.

Antes de copiar, TUDO precisa conferir; qualquer falha => baixa normalmente do
Qobuz e o motivo é mostrado ao usuário:

  1. o banco local conhece a faixa (mesmo ID Qobuz, em qualquer qualidade) e o
     arquivo ainda existe; se o arquivo foi movido/renomeado, ele é achado pela
     tag com o ID (`faixa_existente.py`, mesma regra de faixa avulsa/playlist);
  2. a TAG do arquivo traz esse mesmo ID (e o ISRC, quando os dois lados têm);
  3. o Qobuz (resposta de `track/getFileUrl` desta faixa) confirma o ID e a
     duração (tolerância de 2 s) e a QUALIDADE: o arquivo tem que ser do mesmo
     formato e ter bit depth e taxa de amostragem IGUAIS OU MAIORES que os que o
     Qobuz entrega agora (uma cópia melhor que a pedida é mantida, nunca
     rebaixada nem baixada de novo). Pedido em MP3 com cópia FLAC não reaproveita:
     o nome e a extensão do destino já foram decididos como MP3;
  4. integridade: decodificação completa (ffmpeg), a mesma do
     `verify_after_download`. Sem ffmpeg, ou estourando o tempo, NÃO reaproveita.

As conferências baratas vêm antes da cara (decodificar o arquivo inteiro).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, NamedTuple, Optional

from qobuz_dl.db import handle_download_id
from qobuz_dl.faixa_existente import localizar_faixa_ja_baixada

logger = logging.getLogger(__name__)

TOLERANCIA_DURACAO_S = 2.0
TEMPO_INTEGRIDADE_S = 150.0
_QUALIDADES = (27, 7, 6, 5)

Verificador = Callable[[str], Awaitable[Any]]


class InfoAudio(NamedTuple):
    formato: str  # "flac" ou "mp3"
    bits: int
    taxa_khz: float
    duracao: float
    bitrate: int


@dataclass(frozen=True)
class Esperado:
    """O que o Qobuz diz sobre ESTA faixa, na qualidade pedida."""

    track_id: str = ""
    format_id: Optional[int] = None
    bit_depth: Optional[int] = None
    taxa_khz: Optional[float] = None
    duracao: Optional[float] = None
    isrc: str = ""


@dataclass(frozen=True)
class Reaproveitamento:
    """Resultado: `formato` preenchido = copiou; `motivo` = tinha candidata e recusou."""

    formato: Optional[int] = None
    origem: str = ""
    motivo: str = ""
    detalhes: str = ""


def _numero(valor: Any) -> Optional[float]:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _normalizar_isrc(valor: Any) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isalnum()).upper()


def esperado_do_qobuz(track_url_dict: Any, track_metadata: Any) -> Esperado:
    """Monta o que o Qobuz informa (resposta do getFileUrl + metadados da faixa)."""
    url = track_url_dict if isinstance(track_url_dict, dict) else {}
    meta = track_metadata if isinstance(track_metadata, dict) else {}
    taxa = _numero(url.get("sampling_rate"))
    if taxa is not None and taxa > 1000:
        taxa /= 1000
    bits = _numero(url.get("bit_depth") or url.get("bits_depth"))
    formato = _numero(url.get("format_id"))
    return Esperado(
        track_id=str(url.get("track_id") or ""),
        format_id=int(formato) if formato is not None else None,
        bit_depth=int(bits) if bits is not None else None,
        taxa_khz=taxa,
        duracao=_numero(url.get("duration") or meta.get("duration")),
        isrc=_normalizar_isrc(meta.get("isrc")),
    )


def _descrever(info: InfoAudio) -> str:
    if info.formato == "mp3":
        return f"MP3 {round(info.bitrate / 1000)} kbps"
    return f"FLAC {info.bits}-bit/{info.taxa_khz:g} kHz"


def _mmss(segundos: float) -> str:
    total = int(round(segundos))
    return f"{total // 60}:{total % 60:02d}"


def _maior_que_o_pedido(info: InfoAudio, esperado: Esperado) -> bool:
    """True se o arquivo (FLAC) é de qualidade MAIOR que a que o Qobuz entrega."""
    if (
        info.formato != "flac"
        or esperado.bit_depth is None
        or esperado.taxa_khz is None
    ):
        return False
    return info.bits > esperado.bit_depth or info.taxa_khz > esperado.taxa_khz + 0.05


def conferir_com_qobuz(
    info: InfoAudio, isrc_arquivo: str, esperado: Esperado, track_id: str
) -> str:
    """Devolve o motivo de NÃO reaproveitar, ou "" se tudo confere."""
    if esperado.track_id and esperado.track_id != str(track_id):
        return "o Qobuz devolveu outro ID para esta faixa"
    if esperado.format_id is None:
        return "o Qobuz não informou o formato desta faixa"
    if (
        esperado.duracao is not None
        and abs(info.duracao - esperado.duracao) > TOLERANCIA_DURACAO_S
    ):
        return (
            f"duração diferente (arquivo {_mmss(info.duracao)}, "
            f"Qobuz {_mmss(esperado.duracao)})"
        )
    if esperado.isrc and isrc_arquivo and esperado.isrc != isrc_arquivo:
        return "o ISRC do arquivo não é o desta faixa"
    if esperado.format_id == 5:
        if info.formato != "mp3":
            return f"o Qobuz entrega MP3 e o arquivo é {_descrever(info)}"
        if info.bitrate < 320_000 - 16_000:
            return f"bitrate menor que o MP3 do Qobuz ({_descrever(info)})"
        return ""
    if info.formato != "flac":
        return f"o Qobuz entrega FLAC e o arquivo é {_descrever(info)}"
    if esperado.bit_depth is None or esperado.taxa_khz is None:
        return "o Qobuz não informou bit depth/taxa de amostragem"
    # Igual ou MAIOR serve: a cópia melhor que a pedida é mantida.
    if info.bits < esperado.bit_depth or info.taxa_khz < esperado.taxa_khz - 0.05:
        return (
            f"qualidade menor (arquivo {_descrever(info)}; o Qobuz entrega "
            f"{esperado.bit_depth}-bit/{esperado.taxa_khz:g} kHz)"
        )
    return ""


def pasta_de_origem(caminho: str, base: Any = None) -> str:
    """Pasta do arquivo, relativa à biblioteca (`base`) quando estiver dentro dela."""
    pasta = os.path.dirname(os.path.abspath(caminho))
    if not base:
        return pasta
    raiz = os.path.abspath(os.path.expanduser(str(base)))
    try:
        if os.path.commonpath([pasta, raiz]) != raiz:
            return pasta
    except ValueError:  # discos diferentes (Windows)
        return pasta
    relativa = os.path.relpath(pasta, raiz)
    return "(pasta de downloads)" if relativa == "." else relativa.replace(os.sep, "/")


def _id_gravado_no_arquivo(caminho: str) -> Optional[str]:
    """ID da faixa gravado na tag do arquivo (QDL_TRACK_ID / QOBUZTRACKID)."""
    from qobuz_dl.playlist_links import read_track_id

    valor = read_track_id(caminho)
    return str(valor) if valor else None


def _info_do_arquivo(caminho: str) -> Optional[InfoAudio]:
    """Propriedades REAIS do áudio (não as tags): formato, bits, taxa, duração."""
    extensao = os.path.splitext(caminho)[1].lower()
    try:
        if extensao == ".mp3":
            from mutagen.mp3 import MP3

            # Dependendo da versão dos stubs do mutagen, `MP3.info` é tipado
            # como opcional (mypy: '"None" has no attribute ...') ou não
            # (testar None vira "código inalcançável"). `Any` serve às duas.
            info: Any = MP3(caminho).info
            if info is None:
                return None
            return InfoAudio(
                "mp3",
                0,
                float(info.sample_rate) / 1000,
                float(info.length),
                int(info.bitrate),
            )
        if extensao == ".flac":
            from mutagen.flac import FLAC

            info = FLAC(caminho).info
            if info is None:
                return None
            return InfoAudio(
                "flac",
                int(info.bits_per_sample),
                float(info.sample_rate) / 1000,
                float(info.length),
                int(getattr(info, "bitrate", 0) or 0),
            )
    except Exception:
        logger.debug("Nao consegui ler as propriedades de %s", caminho, exc_info=True)
    return None


def _isrc_do_arquivo(caminho: str) -> str:
    try:
        if caminho.lower().endswith(".mp3"):
            from mutagen.id3 import ID3

            frame = ID3(caminho).get("TSRC")
            return _normalizar_isrc(frame.text[0]) if frame and frame.text else ""
        from mutagen.flac import FLAC

        valores = FLAC(caminho).get("isrc")
        return _normalizar_isrc(valores[0]) if valores else ""
    except Exception:
        return ""


def _inspecionar(origem: str) -> tuple[Optional[InfoAudio], str, str]:
    return (
        _info_do_arquivo(origem),
        _id_gravado_no_arquivo(origem) or "",
        _isrc_do_arquivo(origem),
    )


def _limpar_tags(caminho: str, mp3: bool) -> None:
    """Deixa só o áudio: as tags são reescritas pelo pipeline, no contexto do álbum."""
    if mp3:
        from mutagen import id3

        id3.delete(caminho)
        return
    from mutagen.flac import FLAC

    audio = FLAC(caminho)
    audio.clear_pictures()
    audio.delete()
    audio.save()


def _copiar(origem: str, destino: str, mp3: bool) -> None:
    parcial = destino + ".reuso"
    try:
        shutil.copyfile(origem, parcial)
        _limpar_tags(parcial, mp3)
        os.replace(parcial, destino)
    except BaseException:
        for sobra in (parcial, destino):
            try:
                os.remove(sobra)
            except OSError:
                # Limpeza de melhor esforço: o arquivo pode nem ter sido criado
                # ainda, e o erro que importa é o original, relançado abaixo.
                pass
        raise


async def _candidatas(db_path: Any, track_id: str, quality: Any) -> list[str]:
    """Arquivos que o banco registra para esta faixa (qualidade pedida primeiro).

    Qualquer qualidade serve como candidata: quem decide é a conferência com o
    Qobuz (a qualidade REAL do arquivo tem que ser igual ou maior que a que o
    Qobuz entrega).
    """
    try:
        pedida = int(quality)
    except (TypeError, ValueError):
        return []
    achadas: list[str] = []
    for q in [pedida] + [x for x in _QUALIDADES if x != pedida]:
        try:
            linha = await handle_download_id(db_path, track_id, add_id=False, quality=q)
        except Exception:
            logger.debug(
                "Consulta ao banco falhou; baixando normalmente", exc_info=True
            )
            return achadas
        if not linha or len(linha) < 2 or not linha[1]:
            continue
        status = linha[2] if len(linha) > 2 else "downloaded"
        caminho = str(linha[1])
        if (
            status in (None, "", "downloaded")
            and os.path.isfile(caminho)
            and caminho not in achadas
        ):
            achadas.append(caminho)
    return achadas


async def _tentar(
    origem: str,
    track_id: str,
    destino: str,
    esperado: Esperado,
    verificar: Verificador,
) -> Reaproveitamento:
    def recusa(motivo: str) -> Reaproveitamento:
        return Reaproveitamento(origem=origem, motivo=motivo)

    info, id_tag, isrc_tag = await asyncio.to_thread(_inspecionar, origem)
    if info is None:
        return recusa("não consegui ler o áudio do arquivo")
    if id_tag != track_id:
        return recusa("o ID gravado no arquivo não é o desta faixa")
    motivo = conferir_com_qobuz(info, isrc_tag, esperado, track_id)
    if motivo:
        return recusa(motivo)

    try:
        ok, mensagem = await asyncio.wait_for(
            verificar(origem), timeout=TEMPO_INTEGRIDADE_S
        )
    except asyncio.TimeoutError:
        return recusa("a verificação de integridade excedeu o tempo limite")
    except Exception:
        logger.debug("Verificacao de integridade falhou", exc_info=True)
        return recusa("a verificação de integridade falhou")
    if ok is False:
        return recusa(f"arquivo corrompido ({str(mensagem).strip()[:100]})")
    if ok is not True:
        return recusa(f"integridade não verificável ({str(mensagem).strip()[:100]})")

    try:
        await asyncio.to_thread(_copiar, origem, destino, info.formato == "mp3")
    except Exception:
        logger.debug("Falha ao reaproveitar %s", origem, exc_info=True)
        return recusa("falha ao copiar o arquivo")
    formato = 5 if info.formato == "mp3" else int(esperado.format_id or 0)
    return Reaproveitamento(
        formato=formato,
        origem=origem,
        detalhes=(
            f"ID confere · {_descrever(info)} · {_mmss(info.duracao)} · integridade OK"
            + (
                " · qualidade maior que a pedida, mantida"
                if _maior_que_o_pedido(info, esperado)
                else ""
            )
        ),
    )


async def adotar_copia_com_origem(
    db_path: Any,
    track_id: str,
    quality: Any,
    destino: str,
    esperado: Esperado,
    verificar_integridade: Verificador,
    raiz_biblioteca: Any = None,
) -> Reaproveitamento:
    """Copia para `destino` (temporário do pipeline) uma cópia já baixada e CONFERIDA.

    `formato` do resultado é o `final_fmt` do pipeline (5 = MP3, senão o format_id
    do Qobuz). Sem candidata no banco devolve um `Reaproveitamento()` vazio (sem
    `motivo`); com candidata recusada, `motivo` explica o porquê e o chamador
    baixa normalmente.
    """
    if not db_path or not track_id:
        return Reaproveitamento()
    track_id = str(track_id)
    ultimo = Reaproveitamento()
    # Antes da consulta antiga (`_candidatas`), que APAGA do banco o registro cujo
    # arquivo sumiu: se ele só foi movido/renomeado, a tag com o ID o acha aqui
    # (e o caminho do banco é corrigido).
    achada = None
    if raiz_biblioteca:
        achada = await localizar_faixa_ja_baixada(
            db_path, track_id, quality, raiz_biblioteca
        )
    candidatas = await _candidatas(db_path, track_id, quality)
    if achada and achada.caminho not in candidatas:
        candidatas.insert(0, achada.caminho)
    for origem in candidatas:
        resultado = await _tentar(
            origem, track_id, destino, esperado, verificar_integridade
        )
        if resultado.formato is not None:
            return resultado
        ultimo = resultado
    return ultimo
