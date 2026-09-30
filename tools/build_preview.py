#!/usr/bin/env python3
"""Gera preview.html: a GUI real (index/app.css/app.js) num único arquivo, com
uma API simulada no navegador. Serve para testar cada alteração ao vivo, sem
servidor nem conta Qobuz.   Uso: python tools/build_preview.py [saida.html]"""
import base64, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "qobuz_dl"
WEB, ASSETS = ROOT / "web_ui", ROOT / "gui_assets"
out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview.html")

def data_uri(p: Path) -> str:
    mime = "image/jpeg" if p.suffix == ".jpg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()

html, css, js = (WEB / "index.html").read_text(), (WEB / "app.css").read_text(), (WEB / "app.js").read_text()

def inline_assets(text: str) -> str:
    def sub(m):
        f = ASSETS / m.group(1)
        return data_uri(f) if f.exists() else m.group(0)
    return re.sub(r"/assets/([\w.-]+\.(?:png|jpg))", sub, text)

html = re.sub(r'\s*<link rel="(?:manifest|icon|apple-touch-icon)"[^>]*>', "", html)
html = html.replace('<link rel="stylesheet" href="/app.css">', "<style>" + css + "</style>")
html = re.sub(r'<script defer src="/app.js"></script>', "", html)
covers = {n: data_uri(ASSETS / f"cover-{n}.jpg") for n in ("nebula", "lilac", "forest", "glyph", "amber")}
shim = (Path(__file__).parent / "preview_shim.js").read_text().replace("__COVERS__", __import__("json").dumps(covers))
html = html.replace("</body>", "<script>" + shim + "</script>\n<script>" + js + "</script>\n</body>")
html = inline_assets(html)
out.write_text(html, encoding="utf-8")
print(f"{out} · {out.stat().st_size/1e6:.2f} MB")
