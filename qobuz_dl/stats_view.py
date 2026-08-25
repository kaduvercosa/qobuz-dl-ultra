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

from qobuz_dl import ui
from qobuz_dl.db import get_stats

# Rotulos cujo valor e' longo e deve ser empilhado abaixo do rotulo em
# telas estreitas em vez de estourar a largura da linha.
_STACK_ON_NARROW = ("Bit depths", "Sample rates")


def _fmt_date(value):
    """``2019-05-31T...`` -> ``31/05/2019`` (ou ``?`` quando ausente)."""
    if not value:
        return "?"
    try:
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year}"
    except (ValueError, AttributeError):
        return value


def _row(label, value):
    ui.kv(label, value, narrow_stack=label in _STACK_ON_NARROW)


def render_stats(db_path, show_all_artists=False):
    """Imprime o relatorio de estatisticas. Retorna o codigo de saida."""
    stats = get_stats(db_path)

    ui.banner("QOBUZ-DL-ULTRA  \u00b7  ESTAT\u00cdSTICAS")

    if not stats or stats.get("total", 0) == 0:
        ui.warn(
            "Nenhum dado encontrado. Comece a baixar para popular as estat\u00edsticas."
        )
        ui.rule()
        ui.blank()
        return 0

    total = stats["total"] or 1

    # --- Totais gerais ---
    ui.section("BIBLIOTECA")
    _row("Total de downloads", str(stats["total"]))
    _row("\u00c1lbuns", str(stats["albums"]))
    _row("Faixas avulsas", str(stats["tracks"]))
    _row("Artistas \u00fanicos", str(stats["unique_artists"]))
    _row("\u00c1lbuns \u00fanicos", str(stats["unique_albums"]))
    ui.blank()

    # --- Qualidade ---
    ui.section("QUALIDADE DE \u00c1UDIO")
    hires_pct = stats["hires"] * 100 // total
    met_pct = stats["quality_met"] * 100 // total
    _row("Hi-Res (\u226524bit)", f"{stats['hires']}  ({hires_pct}%)")
    _row("Qualidade solicitada atingida", f"{stats['quality_met']}  ({met_pct}%)")
    _row("Qualidade reduzida", str(stats["quality_not_met"]))

    if stats.get("bit_depths"):
        depths = "  /  ".join(
            f"{k}bit \u2192 {v}" for k, v in list(stats["bit_depths"].items())[:5]
        )
        _row("Bit depths", depths)

    if stats.get("sample_rates"):
        rates = "  /  ".join(
            f"{k}kHz \u2192 {v}" for k, v in list(stats["sample_rates"].items())[:5]
        )
        _row("Sample rates", rates)
    ui.blank()

    # --- Formatos ---
    ui.section("FORMATOS")
    for fmt, count in stats["formats"].items():
        _row(fmt, f"{count}  ({count * 100 // total}%)")
    ui.blank()

    # --- Datas ---
    if stats.get("oldest") or stats.get("newest"):
        ui.section("PER\u00cdODO")
        _row("Lan\u00e7amento mais antigo", _fmt_date(stats["oldest"]))
        _row("Lan\u00e7amento mais recente", _fmt_date(stats["newest"]))
        ui.blank()

    # --- Top artistas ---
    if stats.get("top_artists"):
        ui.section("TOP ARTISTAS")
        peak = stats["top_artists"][0][1] or 1
        narrow = ui.is_narrow()
        # Coluna do nome dimensionada pela largura real: reserva espaco para
        # "  NN. ", a barra e o numero, em vez de fixar 32 colunas (que
        # estourava a linha em terminais de ~72 colunas).
        blocks = max(6, min(24, ui.width() - 56))
        name_col = max(12, ui.width() - 6 - blocks - 6)
        for rank, (artist, count) in enumerate(stats["top_artists"], 1):
            gauge = ui.bar_gauge(count, peak, max_blocks=blocks)
            if narrow:
                # Nome numa linha, barra recuada na linha de baixo.
                ui.emit(f"  {rank:>2}. {ui.truncate(artist, ui.width() - 6)}")
                ui.emit(f"      {gauge} {count}")
            else:
                name = ui.truncate(artist, name_col)
                ui.emit(f"  {rank:>2}. {name:<{name_col}} {gauge} {count}")
        ui.blank()

    # --- Lista completa de artistas ---
    if show_all_artists:
        ui.section(f"TODOS OS ARTISTAS ({stats['unique_artists']})")
        for artist in stats["artist_list"]:
            ui.emit(f"    \u00b7 {ui.truncate(artist, ui.width() - 6)}")
        ui.blank()

    ui.rule()
    if not show_all_artists:
        ui.detail(
            "Dica: use  qobuz-dl stats --artistas  para ver a lista completa.",
            indent=2,
        )
    ui.blank()
    return 0
