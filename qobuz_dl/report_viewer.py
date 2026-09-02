#!/usr/bin/env python3
# ============================================================================
# report_viewer.py -- gera uma pagina HTML autonoma a partir do
# .report.json (arquivo OCULTO gerado por postprocess.py durante os
# downloads -- prefixo com ponto de proposito, ninguem deveria abrir
# esse json na mao).
#
# Estetica "Nothing": preto puro, vermelho de destaque, tipografia
# monoespacada em caixa alta nos rotulos, textura pontilhada (glyph
# interface) e faixas CLICAVEIS -- cada faixa expande pra mostrar os
# detalhes (motivo da falha, badges de letra) so' quando voce clica
# nela, em vez de poluir a lista inteira.
#
# So' uma "vitrine": le o .report.json e produz um .html pra abrir no
# navegador. Nunca escreve de volta no .report.json nem interfere no
# fluxo de download -- pode rodar a qualquer momento, mesmo com
# download em andamento. Funciona 100% offline (CSS+SVG embutidos, sem
# fontes/CDN externos, expandir/recolher faixa e' <details> puro --
# zero JavaScript).
#
# Uso:
#   python3 report_viewer.py /caminho/para/.report.json
#   python3 report_viewer.py /caminho/para/pasta/com/relatorio
#   python3 report_viewer.py /caminho/para/.report.json -o saida.html
#   python3 report_viewer.py /caminho/para/.report.json --abrir   (abre no navegador padrao)
# ============================================================================
import argparse
import html
import json
import os
import sys
import webbrowser

REPORT_FILENAME = ".report.json"


def carregar_report(caminho: str) -> dict:
    if os.path.isdir(caminho):
        caminho = os.path.join(caminho, REPORT_FILENAME)
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def esc(valor) -> str:
    return html.escape(str(valor if valor is not None else ""))


# Glifo de 1 caractere por status -- ecoa a Glyph Interface da Nothing
# (pontos/luzes), sem precisar de icone externo.
_STATUS_LABEL = {
    "concluido": ("Concluída", "ok", "●"),
    "ok": ("Concluída", "ok", "●"),
    "pulada": ("Pulada", "warn", "◐"),
    "falha": ("Falha", "err", "✕"),
    "pendente": ("Pendente", "muted", "○"),
}

_ESTADO_LABEL = {
    "completo": ("Completo", "ok"),
    "incompleto": ("Incompleto", "warn"),
    "em_andamento": ("Em andamento", "muted"),
}

_TIPO_LABEL = {"album": "Álbum", "playlist": "Playlist", "faixa": "Faixa(s)"}


def _fmt_dt(iso_str):
    """'2026-09-02T07:50:37-03:00' -> '02.09.2026 -- 07:50'."""
    if not iso_str:
        return "-"
    try:
        data, resto = iso_str.split("T")
        hora = resto[:5]
        ano, mes, dia = data.split("-")
        return f"{dia}.{mes}.{ano} -- {hora}"
    except Exception:
        return str(iso_str)


def _badge(texto, cor="chip"):
    return f'<span class="badge {cor}">{esc(texto)}</span>'


def _renderizar_faixa(faixa, mostrar_artista_col):
    ident = faixa.get("identificacao", {}) or {}
    download = faixa.get("download", {}) or {}
    letras = faixa.get("letras", {}) or {}

    numero = esc(faixa.get("numero", "-"))
    titulo = esc(ident.get("titulo", "Faixa"))
    status_raw = download.get("situacao", "pendente")
    status_label, status_cor, glifo = _STATUS_LABEL.get(
        status_raw, (status_raw, "muted", "○")
    )
    motivo = download.get("motivo") or ""

    linha_artista = ""
    if mostrar_artista_col and ident.get("artista"):
        linha_artista = f'<div class="track-sub">{esc(ident["artista"])}</div>'

    linha_motivo = (
        f'<div class="track-sub track-motivo">{esc(motivo)}</div>' if motivo else ""
    )

    badges_letras = []
    situacao_letra = letras.get("situacao")
    if situacao_letra == "sucesso":
        if letras.get("sincronizada"):
            badges_letras.append(_badge("Sincronizada", "chip"))
        if letras.get("bilingue"):
            badges_letras.append(_badge("Bilíngue", "chip"))
        elif letras.get("traducao_disponivel") is False:
            badges_letras.append(_badge("Sem tradução PT", "chip-muted"))
        if letras.get("fonte"):
            badges_letras.append(_badge(letras["fonte"], "chip-muted"))
    elif situacao_letra == "falha":
        badges_letras.append(_badge("Falha ao buscar letra", "chip-err"))
    elif situacao_letra == "nao_encontrada":
        badges_letras.append(_badge("Letra não encontrada", "chip-muted"))

    linha_letras = (
        f'<div class="track-badges">{"".join(badges_letras)}</div>'
        if badges_letras
        else ""
    )

    # So' vira <details> CLICAVEL de verdade quando ha' algo pra
    # expandir (motivo de falha ou badges de letra). Faixa "limpa"
    # (concluida, sem letra pra mostrar) fica so' como linha estatica --
    # nao faz sentido um "expandir" que abre vazio.
    tem_detalhe = bool(motivo or badges_letras or linha_artista)

    corpo_expandido = f"""
      {linha_artista}
      {linha_motivo}
      {linha_letras}""".strip()

    marcador = (
        '<span class="track-toggle">＋</span>'
        if tem_detalhe
        else '<span class="track-toggle track-toggle-vazio"></span>'
    )

    if tem_detalhe:
        return f"""
    <details class="track">
      <summary class="track-main">
        <span class="track-num">{numero}</span>
        <span class="track-title">{titulo}</span>
        <span class="status-pill {status_cor}" title="{esc(status_label)}">
          <span class="status-glyph">{glifo}</span>{esc(status_label)}
        </span>
        {marcador}
      </summary>
      <div class="track-body">{corpo_expandido}</div>
    </details>"""

    return f"""
    <div class="track track-static">
      <div class="track-main">
        <span class="track-num">{numero}</span>
        <span class="track-title">{titulo}</span>
        <span class="status-pill {status_cor}" title="{esc(status_label)}">
          <span class="status-glyph">{glifo}</span>{esc(status_label)}
        </span>
        {marcador}
      </div>
    </div>"""


def renderizar_html(report: dict) -> str:
    tipo = report.get("tipo", "faixa")
    ident = report.get("identificacao", {}) or {}
    qualidade = report.get("qualidade", {}) or {}
    progresso = report.get("progresso", {}) or {}
    estado = progresso.get("estado", {}) or {}
    resumo = progresso.get("resumo", {}) or {}
    extra = report.get("extra", {}) or {}
    faixas = report.get("faixas", []) or []

    titulo = ident.get("titulo") or "(sem título)"
    artista = ident.get("artista") or ""
    tipo_lancamento = ident.get("tipo_lancamento") or ""

    subtitulo_partes = [p for p in [artista, _TIPO_LABEL.get(tipo, tipo)] if p]
    subtitulo = " / ".join(subtitulo_partes)

    badges_topo = []
    if tipo_lancamento:
        badges_topo.append(_badge(tipo_lancamento, "chip-accent"))
    if qualidade.get("formato"):
        badges_topo.append(_badge(qualidade["formato"], "chip"))
    if qualidade.get("bit_depth") and qualidade.get("sampling_rate"):
        badges_topo.append(
            _badge(
                f"{qualidade['bit_depth']}BIT / {qualidade['sampling_rate']}KHZ",
                "chip",
            )
        )
    alvo = qualidade.get("alvo_atingida")
    if alvo is True:
        badges_topo.append(_badge("Qualidade atingida", "chip-ok"))
    elif alvo is False:
        badges_topo.append(_badge("Qualidade não atingida", "chip-warn"))

    situacao_estado = estado.get("situacao", "em_andamento")
    estado_label, estado_cor = _ESTADO_LABEL.get(
        situacao_estado, (situacao_estado, "muted")
    )

    total = resumo.get("total", len(faixas)) or 0
    concluidas = resumo.get("concluidas", 0)
    pct = int(round((concluidas / total) * 100)) if total else 0

    linha_extra = []
    if extra.get("rotulo"):
        linha_extra.append(
            f'<div class="meta-row"><span class="meta-glyph">▪</span>{esc(extra["rotulo"])}</div>'
        )
    if extra.get("genero"):
        linha_extra.append(
            f'<div class="meta-row"><span class="meta-glyph">▪</span>{esc(extra["genero"])}</div>'
        )
    if extra.get("url") or ident.get("url"):
        url = extra.get("url") or ident.get("url")
        linha_extra.append(
            f'<div class="meta-row"><span class="meta-glyph">▪</span><a href="{esc(url)}">{esc(url)}</a></div>'
        )

    artistas_distintos = {
        f.get("identificacao", {}).get("artista")
        for f in faixas
        if f.get("identificacao", {}).get("artista")
    }
    mostrar_artista_col = len(artistas_distintos) > 1

    faixas_html = (
        "".join(_renderizar_faixa(f, mostrar_artista_col) for f in faixas)
        or '<div class="empty">Nenhuma faixa registrada ainda.</div>'
    )

    resumo_stats = "".join(
        f'<div class="stat"><div class="stat-num">{resumo.get(chave, 0):02d}</div>'
        f'<div class="stat-label">{rotulo}</div></div>'
        for chave, rotulo in [
            ("concluidas", "FEITO"),
            ("puladas", "PULO"),
            ("falhas", "ERRO"),
            ("pendentes", "ESPERA"),
        ]
    )

    # Barra de progresso em segmentos (dots), no lugar de gradiente liso
    # -- mesma linguagem visual do glyph/dot-matrix da Nothing.
    total_segmentos = 20
    segmentos_cheios = int(round((pct / 100) * total_segmentos)) if total else 0
    barra_segmentos = "".join(
        f'<span class="seg {"on" if i < segmentos_cheios else ""}"></span>'
        for i in range(total_segmentos)
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)} // relatório</title>
<style>
  :root {{
    --bg: #ffffff; --card: #ffffff; --border: #ddd9d0; --border-soft: #ece8e0;
    --text: #17170f; --text-2: #57554c; --text-3: #8c897e;
    --red: #d61f26; --red-dim: #fbe0e0;
    --ok: #1c7a3a; --ok-dim: #dcf0e2;
    --warn: #a85c00; --warn-dim: #f6e4c9;
    --err: #c62828; --err-dim: #f8d9d9;
    --chip-bg: #f4f2ec; --chip-text: #3a382f;
    --dot: rgba(23,23,15,0.07);
  }}
  * {{ box-sizing: border-box; -webkit-font-smoothing: antialiased; }}
  html {{ background: var(--bg); }}
  body {{
    margin: 0; padding: 28px 14px 72px; background: var(--bg); color: var(--text);
    background-image: radial-gradient(var(--dot) 1px, transparent 1px);
    background-size: 14px 14px;
    font-family: Menlo, Monaco, "SF Mono", Consolas, "Liberation Mono", "Courier New", ui-monospace, monospace;
    display: flex; justify-content: center;
  }}
  .card {{
    width: 100%; max-width: 480px; background: var(--card);
    border: 1px solid var(--border); border-radius: 4px; padding: 22px 18px;
    position: relative;
    box-shadow: 0 1px 3px rgba(23,23,15,0.06);
  }}
  .card::before, .card::after {{
    content: ""; position: absolute; width: 7px; height: 7px;
    border: 1px solid var(--text-3); top: 10px;
  }}
  .card::before {{ left: 10px; border-right: none; border-bottom: none; }}
  .card::after {{ right: 10px; border-left: none; border-bottom: none; }}

  .kicker {{
    font-size: 10px; letter-spacing: 2.5px; color: var(--text-3);
    text-transform: uppercase; margin: 0 0 14px; display: flex;
    justify-content: space-between; align-items: center;
  }}
  .kicker .rec {{ color: var(--red); }}

  .header-row {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
  .title {{ font-size: 19px; font-weight: 600; margin: 0; line-height: 1.35; letter-spacing: 0.1px; }}
  .subtitle {{
    font-size: 11px; color: var(--text-2); margin: 6px 0 0;
    text-transform: uppercase; letter-spacing: 1.2px;
  }}
  .estado-pill {{
    font-size: 10px; font-weight: 600; padding: 4px 9px;
    border-radius: 3px; white-space: nowrap; flex-shrink: 0;
    text-transform: uppercase; letter-spacing: 1px;
  }}
  .estado-pill.ok {{ color: var(--ok); background: var(--ok-dim); }}
  .estado-pill.warn {{ color: var(--warn); background: var(--warn-dim); }}
  .estado-pill.muted {{ color: var(--text-2); background: var(--chip-bg); }}

  .badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 16px 0 0; }}
  .badge {{
    font-size: 10px; padding: 4px 8px; border-radius: 3px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.8px;
  }}
  .badge.chip {{ background: var(--chip-bg); color: var(--chip-text); }}
  .badge.chip-muted {{ background: transparent; color: var(--text-3); border: 1px solid var(--border-soft); }}
  .badge.chip-accent {{ background: var(--red-dim); color: var(--red); }}
  .badge.chip-ok {{ background: var(--ok-dim); color: var(--ok); }}
  .badge.chip-warn {{ background: var(--warn-dim); color: var(--warn); }}
  .badge.chip-err {{ background: var(--err-dim); color: var(--err); }}

  .meta {{ margin: 14px 0 0; }}
  .meta-row {{ font-size: 12px; color: var(--text-2); margin: 4px 0; display: flex; align-items: center; gap: 6px; }}
  .meta-glyph {{ color: var(--red); font-size: 8px; }}
  .meta-row a {{ color: var(--text); text-decoration: none; word-break: break-all; border-bottom: 1px dotted var(--text-3); }}

  .progress-wrap {{ margin: 20px 0 4px; }}
  .progress-top {{
    display: flex; justify-content: space-between; font-size: 10px; color: var(--text-2);
    margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1.2px;
  }}
  .progress-top .pct {{ color: var(--red); }}
  .seg-track {{ display: flex; gap: 3px; }}
  .seg {{ flex: 1; height: 8px; background: var(--chip-bg); border-radius: 1px; }}
  .seg.on {{ background: var(--red); }}

  .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 18px 0 0; background: var(--border-soft); border: 1px solid var(--border-soft); }}
  .stat {{ background: var(--card); padding: 10px 4px; text-align: center; }}
  .stat-num {{ font-size: 17px; font-weight: 700; letter-spacing: 1px; }}
  .stat-label {{ font-size: 9px; color: var(--text-3); margin-top: 3px; text-transform: uppercase; letter-spacing: 1.4px; }}

  .tracks {{ border-top: 1px dashed var(--border); margin-top: 18px; }}
  .track {{ border-bottom: 1px dashed var(--border-soft); }}
  .track-static {{ border-bottom: 1px dashed var(--border-soft); padding: 11px 0; }}
  .track:last-child, .track-static:last-child {{ border-bottom: none; }}

  details.track summary {{ list-style: none; cursor: pointer; padding: 11px 0; }}
  details.track summary::-webkit-details-marker {{ display: none; }}
  details.track[open] {{ padding-bottom: 10px; }}
  details.track[open] summary {{ padding-bottom: 4px; }}

  .track-main {{ display: flex; align-items: center; gap: 10px; }}
  .track-num {{ font-size: 11px; color: var(--text-3); width: 20px; flex-shrink: 0; font-variant-numeric: tabular-nums; }}
  .track-title {{ flex: 1; min-width: 0; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

  .status-pill {{
    font-size: 10px; font-weight: 600; padding: 3px 8px; border-radius: 3px;
    white-space: nowrap; flex-shrink: 0; display: flex; align-items: center; gap: 5px;
    text-transform: uppercase; letter-spacing: 0.6px;
  }}
  .status-pill.ok {{ color: var(--ok); background: var(--ok-dim); }}
  .status-pill.warn {{ color: var(--warn); background: var(--warn-dim); }}
  .status-pill.err {{ color: var(--err); background: var(--err-dim); }}
  .status-pill.muted {{ color: var(--text-2); background: var(--chip-bg); }}
  .status-glyph {{ font-size: 9px; }}

  .track-toggle {{
    width: 16px; height: 16px; flex-shrink: 0; display: flex; align-items: center;
    justify-content: center; font-size: 12px; color: var(--text-3);
    border: 1px solid var(--border-soft); border-radius: 3px; transition: transform 0.15s ease;
  }}
  details.track[open] .track-toggle {{ transform: rotate(45deg); color: var(--red); border-color: var(--red-dim); background: var(--red-dim); }}
  .track-toggle-vazio {{ border-color: transparent; }}

  .track-body {{ padding: 2px 0 4px 30px; }}
  .track-sub {{ font-size: 11.5px; color: var(--text-2); margin: 3px 0; }}
  .track-motivo {{ color: var(--err); }}
  .track-badges {{ display: flex; flex-wrap: wrap; gap: 5px; margin: 6px 0 0; }}
  .track-badges .badge {{ font-size: 9.5px; padding: 2px 7px; }}

  .footer {{
    font-size: 9.5px; color: var(--text-3); margin-top: 22px; text-align: center;
    text-transform: uppercase; letter-spacing: 1.2px;
  }}
  .empty {{ font-size: 12px; color: var(--text-3); padding: 18px 0; text-align: center; text-transform: uppercase; letter-spacing: 1px; }}
</style>
</head>
<body>
  <div class="card">
    <p class="kicker"><span>Relatório <span class="rec">●</span> Download</span><span>{esc(tipo.upper())}</span></p>

    <div class="header-row">
      <div>
        <p class="title">{esc(titulo)}</p>
        <p class="subtitle">{esc(subtitulo)}</p>
      </div>
      <span class="estado-pill {estado_cor}">{esc(estado_label)}</span>
    </div>

    <div class="badges">{"".join(badges_topo)}</div>

    <div class="meta">{"".join(linha_extra)}</div>

    <div class="progress-wrap">
      <div class="progress-top">
        <span>Progresso</span>
        <span><span class="pct">{pct}%</span> -- {concluidas}/{total}</span>
      </div>
      <div class="seg-track">{barra_segmentos}</div>
    </div>

    <div class="stats">{resumo_stats}</div>

    <div class="tracks">{faixas_html}</div>

    <div class="footer">
      Criado {_fmt_dt(estado.get("criado_em"))} · Atualizado {_fmt_dt(estado.get("atualizado_em"))}
    </div>
  </div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(
        description="Gera uma pagina HTML a partir de um .report.json."
    )
    parser.add_argument(
        "caminho", help="Caminho do .report.json ou da pasta que o contem"
    )
    parser.add_argument(
        "-o", "--saida", help="Arquivo .html de saida (padrao: ao lado do .report.json)"
    )
    parser.add_argument(
        "--abrir", action="store_true", help="Abre o resultado no navegador padrao"
    )
    args = parser.parse_args()

    try:
        report = carregar_report(args.caminho)
    except FileNotFoundError:
        print(f"Nao encontrei {REPORT_FILENAME} em: {args.caminho}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{REPORT_FILENAME} invalido/corrompido: {e}", file=sys.stderr)
        sys.exit(1)

    pasta_base = (
        args.caminho
        if os.path.isdir(args.caminho)
        else os.path.dirname(args.caminho) or "."
    )
    saida = args.saida or os.path.join(pasta_base, "report.html")

    with open(saida, "w", encoding="utf-8") as f:
        f.write(renderizar_html(report))

    print(f"Gerado: {saida}")
    if args.abrir:
        webbrowser.open(f"file://{os.path.abspath(saida)}")


if __name__ == "__main__":
    main()
