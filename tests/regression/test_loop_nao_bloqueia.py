"""O event loop não pode ficar parado em I/O de disco ou de rede síncrono.

Dois pontos que travavam o loop enquanto outras tarefas (downloads, buscas)
esperavam a vez:

* `postprocess`: a cada faixa, o `.report.json` e o `report.html` inteiros eram
  lidos e regerados de forma síncrona;
* `retro_tagger`: leitura de tags (mutagen), `os.walk` e, principalmente,
  `engine.fetch_and_inject` -- que usa um cliente HTTP *síncrono* -- rodavam
  direto no loop.

Os testes verificam em qual thread cada função bloqueante é executada (teste
determinístico, sem depender de tempo) e, para o relatório, também medem se o
loop continua respondendo enquanto a gravação demora.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from qobuz_dl import postprocess as pp
from qobuz_dl import retro_tagger as rt

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# postprocess: relatório
# --------------------------------------------------------------------------
async def test_relatorio_le_e_grava_fora_do_loop(tmp_path, monkeypatch):
    principal = threading.get_ident()
    threads = []
    carregar, gravar = pp._load_report, pp._save_report

    def carregar_espiao(path):
        threads.append(("load", threading.get_ident()))
        return carregar(path)

    def gravar_espiao(path, report):
        threads.append(("save", threading.get_ident()))
        return gravar(path, report)

    monkeypatch.setattr(pp, "_load_report", carregar_espiao)
    monkeypatch.setattr(pp, "_save_report", gravar_espiao)

    pasta = str(tmp_path)
    await pp.init_report(pasta, tipo="album", titulo="A")
    await pp.update_track_status(
        pasta, numero="01", item_id="t1", titulo="X", status="ok"
    )
    await pp.finalize_report(pasta, completo=True)

    operacoes = {op for op, _ in threads}
    assert operacoes == {"load", "save"}
    assert all(t != principal for _, t in threads), threads


async def test_loop_continua_respondendo_durante_a_gravacao(tmp_path, monkeypatch):
    gravar = pp._save_report

    def gravar_lento(path, report):
        time.sleep(0.3)  # disco lento / report.html grande
        return gravar(path, report)

    monkeypatch.setattr(pp, "_save_report", gravar_lento)

    maior_intervalo = 0.0
    parar = asyncio.Event()

    async def monitor():
        nonlocal maior_intervalo
        anterior = time.perf_counter()
        while not parar.is_set():
            await asyncio.sleep(0.01)
            agora = time.perf_counter()
            maior_intervalo = max(maior_intervalo, agora - anterior)
            anterior = agora

    tarefa = asyncio.create_task(monitor())
    await asyncio.sleep(0.05)
    await pp.init_report(str(tmp_path), tipo="album", titulo="A")
    parar.set()
    await tarefa

    # Com a gravação no loop o intervalo passaria de 0,3 s.
    assert maior_intervalo < 0.15, maior_intervalo


async def test_atualizacoes_concorrentes_da_mesma_pasta_nao_se_perdem(tmp_path):
    pasta = str(tmp_path)
    await pp.init_report(pasta, tipo="album", titulo="A")
    await asyncio.gather(
        *(
            pp.update_track_status(
                pasta, numero=f"{i:02d}", item_id=f"t{i}", titulo=f"F{i}", status="ok"
            )
            for i in range(1, 9)
        )
    )
    import json
    import os

    with open(os.path.join(pasta, pp.REPORT_FILENAME), encoding="utf-8") as f:
        texto = f.read()
    assert len(json.loads(texto)) > 0
    for i in range(1, 9):
        assert f'"t{i}"' in texto, i


# --------------------------------------------------------------------------
# retro_tagger: letras retroativas
# --------------------------------------------------------------------------
class _Engine:
    def __init__(self, *a, **k):
        self.threads = []

    def fetch_and_inject(self, **kwargs):
        self.threads.append(threading.get_ident())
        return {"success": True}

    def close(self):
        pass


async def test_letras_retroativas_nao_bloqueiam_o_loop(tmp_path, monkeypatch):
    principal = threading.get_ident()
    onde = {}

    def espiao(nome, valor):
        def f(*a, **k):
            onde[nome] = threading.get_ident()
            return valor

        return f

    (tmp_path / "Artista - Faixa.flac").write_bytes(b"x")
    engine = _Engine()
    monkeypatch.setattr(rt.ui, "emit", lambda *a, **k: None)
    monkeypatch.setattr(rt, "LyricsEngine", lambda *a, **k: engine)
    monkeypatch.setattr(rt, "extract_track_id", espiao("extract_track_id", "100"))
    monkeypatch.setattr(rt, "_lyrics_lookup_hints", espiao("hints", {}))
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        espiao(
            "inspect",
            {
                "has_lyrics": False,
                "is_bilingual": False,
                "content": "",
                "lrc_exists": False,
                "language": None,
            },
        ),
    )
    monkeypatch.setattr(
        rt, "FLAC", espiao("FLAC", {"TITLE": ["Faixa"], "ARTIST": ["Artista"]})
    )

    async def buscar(cli, tid, language=None):
        return {"original": {"lang": "pt", "lines": [{"text": "x"}]}}

    monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar)

    await rt.process_retroactive_lyrics_async(
        str(tmp_path),
        SimpleNamespace(),
        settings=SimpleNamespace(
            lyrics_translation_lang="pt", lrc_files=True, embed_lyrics=True
        ),
    )

    assert set(onde) == {"FLAC", "hints", "extract_track_id", "inspect"}
    assert engine.threads, "fetch_and_inject não foi chamado"
    for nome, tid in {**onde, "fetch_and_inject": engine.threads[0]}.items():
        assert tid != principal, f"{nome} rodou no loop"


def test_coletar_arquivos_so_flac_e_mp3_em_ordem(tmp_path):
    (tmp_path / "sub").mkdir()
    for nome in ("b.flac", "a.mp3", "c.txt", "sub/d.FLAC", "sub/e.jpg"):
        (tmp_path / nome).write_bytes(b"x")
    achados = [p.replace(str(tmp_path), "") for p in rt._collect_audio_files(tmp_path)]
    assert achados == sorted(achados)
    assert {a.lstrip("/\\") for a in achados} == {"b.flac", "a.mp3", "sub/d.FLAC"} or {
        a.lstrip("/\\").replace("\\", "/") for a in achados
    } == {"b.flac", "a.mp3", "sub/d.FLAC"}


def test_ler_tags_basicas_flac_e_erro(monkeypatch):
    monkeypatch.setattr(
        rt, "FLAC", lambda p: {"TITLE": ["T"], "ALBUMARTIST": ["AA"], "ALBUM": ["Al"]}
    )
    assert rt._read_basic_tags("x.flac", ".flac") == ("T", "AA", "Al")

    def quebra(p):
        raise OSError("ruim")

    monkeypatch.setattr(rt, "FLAC", quebra)
    assert rt._read_basic_tags("x.flac", ".flac") == ("", "", "")
    assert rt._read_basic_tags("x.wav", ".wav") == ("", "", "")
