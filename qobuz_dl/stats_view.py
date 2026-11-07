"""Renderizacao do comando ``qobuz-dl stats``.

Este codigo vivia inline dentro de ``cli.async_main()`` -- cerca de 130 linhas
de layout de terminal no meio da funcao de roteamento de comandos, que ainda
por cima faziam parsing manual de ``sys.argv[2] == "--artistas"`` mesmo com o
argparse (``commands.stats_args``) ja' registrando essa flag corretamente.

Agora e' um modulo proprio que:
  * recebe os argumentos ja' parseados pelo argparse (sem tocar em sys.argv);
  * desenha tudo pela camada unica ``qobuz_dl.ui`` (uma largura, um conjunto
    de breakpoints, um lock de terminal);
  * retorna o codigo de saida em vez de chamar ``sys.exit()`` por conta
    propria, o que torna a funcao testavel.
"""

from typing import Any

from qobuz_dl import ui
from qobuz_dl.db import get_stats

# Largura dos paineis: o mesmo desenhista de caixas do resto do programa
# (`ui.header`/`ui.emit_box`), com teto proprio; em terminal estreito a caixa
# encolhe sozinha. Rotulos curtos: abaixo de ~40 colunas o painel limita o
# rotulo a metade da largura interna.
_COLS_PAINEL = ui.PAINEL_COLS
_NOME_MIN = 14  # abaixo disso o nome do artista fica pequeno demais: empilha
_GLIFO_LISTA = "\u00b7"


def _fmt_date(value: Any) -> Any:
    """``2019-05-31T...`` -> ``31/05/2019`` (ou ``?`` quando ausente)."""
    if not value:
        return "?"
    try:
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year}"
    except (ValueError, AttributeError):
        return value


def _painel(titulo: str, linhas: list, primeiro: bool = False) -> None:
    """Painel de pares rotulo/valor (um por secao do relatorio)."""
    ui.header(titulo, linhas, cols=_COLS_PAINEL, lead=primeiro)


def _linhas_top_artistas(top: list) -> list:
    """Linhas do ranking: nome + barra na mesma linha, ou empilhadas se estreito."""
    inner = ui.box_width(_COLS_PAINEL) - 4
    peak = top[0][1] or 1
    largura_contagem = len(str(max(count for _, count in top)))
    blocos = max(6, min(20, inner // 4))
    # "NN. " + nome + " " + barra + " " + contagem
    nome_col = inner - 4 - 1 - blocos - 1 - largura_contagem
    linhas = []
    for rank, (artist, count) in enumerate(top, 1):
        if nome_col >= _NOME_MIN:
            gauge = ui.bar_gauge(count, peak, max_blocks=blocos)
            nome = ui.pad_cells(ui.truncate(artist, nome_col), nome_col)
            linhas.append(f"{rank:>2}. {nome} {gauge} {count}")
        else:
            # Nome numa linha, barra recuada na linha de baixo.
            blocos_estreito = max(3, inner - 4 - 1 - largura_contagem)
            gauge = ui.bar_gauge(count, peak, max_blocks=min(20, blocos_estreito))
            linhas.append(f"{rank:>2}. {ui.truncate(artist, inner - 4)}")
            linhas.append(f"    {gauge} {count}")
    return linhas


def render_stats(
    db_path: Any, show_all_artists: bool = False, como_json: bool = False
) -> int:
    """Imprime o relatorio de estatisticas. Retorna o codigo de saida.

    Com ``como_json`` sai so o JSON das estatisticas (as mesmas chaves de
    ``db.get_stats``; ``artist_list`` so entra com ``show_all_artists``). Banco
    vazio ou ausente vira o dicionario zerado, nunca texto.
    """
    stats = get_stats(db_path)

    if como_json:
        dados = dict(stats or {})
        if not show_all_artists:
            dados.pop("artist_list", None)
        ui.emit_json(dados)
        return 0

    if not stats or stats.get("total", 0) == 0:
        ui.warn(
            "Nenhum dado encontrado. Comece a baixar para popular as estat\u00edsticas."
        )
        ui.blank()
        return 0

    total = stats["total"] or 1

    # --- Totais gerais ---
    _painel(
        "BIBLIOTECA",
        [
            ("Downloads", str(stats["total"])),
            ("\u00c1lbuns", str(stats["albums"])),
            ("Avulsas", str(stats["tracks"])),
            ("Artistas", str(stats["unique_artists"])),
            ("\u00c1lbuns \u00fanicos", str(stats["unique_albums"])),
        ],
        primeiro=True,
    )

    # --- Qualidade ---
    hires_pct = stats["hires"] * 100 // total
    met_pct = stats["quality_met"] * 100 // total
    qualidade = [
        ("Hi-Res \u226524bit", f"{stats['hires']}  ({hires_pct}%)"),
        ("Meta atingida", f"{stats['quality_met']}  ({met_pct}%)"),
        ("Reduzida", str(stats["quality_not_met"])),
    ]
    if stats.get("bit_depths"):
        depths = "  /  ".join(
            f"{k}bit\u2192{v}" for k, v in list(stats["bit_depths"].items())[:5]
        )
        qualidade.append(("Bit depths", depths))
    if stats.get("sample_rates"):
        rates = "  /  ".join(
            f"{k}kHz\u2192{v}" for k, v in list(stats["sample_rates"].items())[:5]
        )
        qualidade.append(("Sample rates", rates))
    _painel("QUALIDADE DE \u00c1UDIO", qualidade)

    # --- Formatos ---
    _painel(
        "FORMATOS",
        [
            (fmt, f"{count}  ({count * 100 // total}%)")
            for fmt, count in stats["formats"].items()
        ],
    )

    # --- Datas ---
    if stats.get("oldest") or stats.get("newest"):
        _painel(
            "PER\u00cdODO",
            [
                ("Mais antigo", _fmt_date(stats["oldest"])),
                ("Mais recente", _fmt_date(stats["newest"])),
            ],
        )

    # --- Top artistas ---
    if stats.get("top_artists"):
        ui.emit_box(
            "TOP ARTISTAS",
            _linhas_top_artistas(stats["top_artists"]),
            cols=_COLS_PAINEL,
            lead=False,
        )

    # --- Lista completa de artistas ---
    if show_all_artists:
        inner = ui.box_width(_COLS_PAINEL) - 4
        ui.emit_box(
            f"TODOS OS ARTISTAS ({stats['unique_artists']})",
            [
                f"{_GLIFO_LISTA} {ui.truncate(artist, inner - 2)}"
                for artist in stats["artist_list"]
            ],
            cols=_COLS_PAINEL,
            lead=False,
        )

    if not show_all_artists:
        ui.detail(
            "Dica: use  qobuz-dl stats --artistas  para ver a lista completa.",
            indent=2,
        )
    ui.blank()
    return 0
