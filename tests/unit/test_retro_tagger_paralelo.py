"""Letras retroativas: sequencial por padrão, paralelo com `max_workers`.

O número de arquivos processados ao mesmo tempo vem de `settings.max_workers`,
o mesmo limite usado nos downloads. O relatório final mantém a ordem dos
arquivos mesmo quando as operações terminam fora de ordem.
"""

import asyncio
import os
import re
import threading
import time
from types import SimpleNamespace

import pytest

from qobuz_dl import retro_tagger as rt

pytestmark = pytest.mark.unit

NOMES = [f"f{i}.flac" for i in range(1, 7)]


class _Engine:
    """Engine falsa: `fetch_and_inject` demora e mede quantas rodam juntas."""

    def __init__(self, atraso=0.1, falha_em=None):
        self.atraso, self.falha_em = atraso, falha_em
        self.ativas = self.pico = 0
        self.ordem = []
        self.fechada = False
        self._lock = threading.Lock()

    def fetch_and_inject(self, **kw):
        nome = os.path.basename(kw["file_path"])
        with self._lock:
            self.ativas += 1
            self.pico = max(self.pico, self.ativas)
            self.ordem.append(nome)
        try:
            time.sleep(self.atraso)
            if nome == self.falha_em:
                raise RuntimeError("falhou em " + nome)
            return {"success": True}
        finally:
            with self._lock:
                self.ativas -= 1

    def close(self):
        self.fechada = True


@pytest.fixture
def rodar(tmp_path, monkeypatch):
    saida = []
    monkeypatch.setattr(rt.ui, "emit", lambda texto="", end="\n": saida.append(texto))
    for nome in NOMES:
        (tmp_path / nome).write_bytes(b"x")

    monkeypatch.setattr(rt, "extract_track_id", lambda p: "100")
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        lambda p: {
            "has_lyrics": False,
            "is_bilingual": False,
            "is_synced": False,
            "content": "",
            "lrc_exists": False,
            "language": None,
        },
    )
    monkeypatch.setattr(
        rt,
        "FLAC",
        lambda p: {
            "TITLE": [os.path.splitext(os.path.basename(p))[0]],
            "ARTIST": ["A"],
        },
    )

    async def buscar(cli, tid, language=None):
        return {"original": {"lang": "pt", "lines": [{"text": "x"}]}}

    monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar)

    async def _rodar(engine, **settings_extra):
        monkeypatch.setattr(rt, "LyricsEngine", lambda *a, **k: engine)
        settings = SimpleNamespace(
            lyrics_translation_lang="pt",
            lrc_files=True,
            embed_lyrics=True,
            **settings_extra,
        )
        inicio = time.perf_counter()
        await rt.process_retroactive_lyrics_async(
            str(tmp_path), SimpleNamespace(), settings=settings
        )
        _rodar.duracao = time.perf_counter() - inicio

    _rodar.saida = saida
    # Cada faixa sai num bloco "[NN/total] Artista - nome": devolve os nomes na
    # ordem em que os blocos foram impressos.
    _rodar.nomes_no_relatorio = lambda: [
        m.group(1)
        for linha in "\n".join(saida).splitlines()
        if (m := re.match(r"\[\d+/\d+\] A - (\S+)", linha))
    ]
    _rodar.indices = lambda: re.findall(r"^\[(\d+)/\d+\]", "\n".join(saida), re.M)
    return _rodar


async def test_padrao_e_sequencial_e_em_ordem(rodar):
    engine = _Engine()
    await rodar(engine)  # sem max_workers nas configurações
    assert engine.pico == 1
    assert engine.ordem == NOMES
    assert rodar.nomes_no_relatorio() == [n[:-5] for n in NOMES]
    assert rodar.indices() == [str(n) for n in range(1, len(NOMES) + 1)]
    assert engine.fechada


async def test_max_workers_processa_varios_ao_mesmo_tempo(rodar):
    engine = _Engine(atraso=0.15)
    await rodar(engine, max_workers=3)
    assert engine.pico == 3
    assert sorted(engine.ordem) == NOMES
    # 6 arquivos x 0,15 s: sequencial levaria >= 0,9 s; com 3 em paralelo ~0,3 s.
    assert rodar.duracao < 0.7, rodar.duracao


async def test_relatorio_sai_na_ordem_dos_arquivos_mesmo_com_conclusao_trocada(
    rodar,
):
    class _Invertido(_Engine):
        def fetch_and_inject(self, **kw):
            # os primeiros arquivos demoram mais e terminam por último
            n = int(os.path.basename(kw["file_path"])[1])
            self.atraso = 0.05 * (7 - n)
            return super().fetch_and_inject(**kw)

    engine = _Invertido()
    await rodar(engine, max_workers=4)
    assert rodar.nomes_no_relatorio() == [n[:-5] for n in NOMES]
    assert rodar.indices() == [str(n) for n in range(1, len(NOMES) + 1)]


async def test_numero_de_workers_tem_teto(rodar):
    engine = _Engine()
    await rodar(engine, max_workers=16)
    assert engine.pico <= 3


async def test_falha_em_um_arquivo_cancela_o_resto_e_fecha_a_engine(rodar):
    engine = _Engine(atraso=0.05, falha_em="f2.flac")
    with pytest.raises(RuntimeError, match="f2.flac"):
        await rodar(engine, max_workers=3)
    assert engine.fechada
    # nenhuma tarefa ficou solta depois do erro
    pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pendentes == []


@pytest.mark.parametrize(
    "valor, esperado",
    [(None, 1), ("x", 1), (0, 1), (-3, 1), (1, 1), (2, 2), ("3", 3), (4, 3), (99, 3)],
)
def test_lyrics_workers(valor, esperado):
    assert rt._lyrics_workers(SimpleNamespace(max_workers=valor)) == esperado


def test_lyrics_workers_sem_o_atributo():
    assert rt._lyrics_workers(SimpleNamespace()) == 1


# --------------------------------------------------------------------------
# Ligação com o comando `lyrics`
# --------------------------------------------------------------------------
def test_comando_lyrics_aceita_max_workers():
    from qobuz_dl.commands import qobuz_dl_args

    parser = qobuz_dl_args()
    assert parser.parse_args(["lyrics"]).max_workers is None
    assert parser.parse_args(["lyrics", "--max-workers", "3", "/m"]).max_workers == 3


@pytest.mark.parametrize("valor, esperado", [("1", 1), ("3", 3), (16, 3), ("lixo", 1)])
def test_max_workers_das_configuracoes_chega_ao_limite_das_letras(valor, esperado):
    from qobuz_dl.settings import QobuzDLSettings

    config = QobuzDLSettings(max_workers=valor)
    assert rt._lyrics_workers(config) == esperado
