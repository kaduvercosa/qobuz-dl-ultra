"""Testa qobuz_dl/watcher.py: o handler de eventos do watchdog e o loop de
debounce que agrupa arquivos novos antes de disparar o retro-tagging.

O QUE ESTE ARQUIVO NÃO FAZ
---------------------------
Não usa a lib `watchdog` de verdade (nem `Observer`, nem eventos reais do
sistema de arquivos) -- só testaria se o watchdog funciona, não se a lógica
do projeto está certa. `Observer` é substituído por `_ObserverFalso`, que
captura o handler registrado; os "eventos" são objetos simples
(`SimpleNamespace`) com os atributos que `_NewAudioFileHandler` realmente
lê (`is_directory`, `src_path`/`dest_path`).

POR QUE on_created E on_moved SÃO TESTADOS SEPARADOS
-----------------------------------------------------
`on_created` enfileira `event.src_path`; `on_moved` enfileira
`event.dest_path` (o nome FINAL do arquivo, depois do rename) -- não
`src_path`. Trocar os dois é um erro fácil de cometer copiando um método
pro outro, e silencioso: o programa continuaria rodando, só ficaria
observando/enfileirando a pasta errada em downloads que criam um arquivo
temporário e renomeiam pro nome final (padrão comum).
"""

import asyncio
from types import SimpleNamespace

import pytest

from qobuz_dl import watcher


class _LoopFalso:
    """Substitui `call_soon_threadsafe` por uma chamada direta e síncrona.

    No código real isso importa porque o watchdog roda numa thread
    separada da do asyncio; no teste, tudo roda na mesma coroutine, então
    chamar direto é equivalente e mais simples de inspecionar.
    """

    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class _ObserverFalso:
    """Substitui `watchdog.observers.Observer`: não observa nada de
    verdade, só guarda o handler registrado pra o teste poder disparar
    eventos manualmente."""

    def __init__(self):
        self.handler = None
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler, directory, recursive=True):
        self.handler = handler

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self):
        self.joined = True


def _evento(path, *, movido=False, is_directory=False):
    if movido:
        return SimpleNamespace(is_directory=is_directory, dest_path=path)
    return SimpleNamespace(is_directory=is_directory, src_path=path)


# --------------------------------------------------------------------
# _NewAudioFileHandler -- lógica síncrona do handler
# --------------------------------------------------------------------
class TestNewAudioFileHandler:
    async def test_extensao_de_audio_e_enfileirada(self):
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)

        handler.on_created(_evento("/musicas/Artista/Album/01 - Faixa.flac"))

        assert fila.qsize() == 1
        assert fila.get_nowait() == "/musicas/Artista/Album"

    def test_extensao_e_case_insensitive(self):
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)
        handler.on_created(_evento("/musicas/Faixa.FLAC"))
        assert fila.qsize() == 1

    def test_extensao_nao_suportada_e_ignorada(self):
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)
        handler.on_created(_evento("/musicas/capa.jpg"))
        handler.on_created(_evento("/musicas/booklet.pdf"))
        assert fila.qsize() == 0

    def test_evento_de_diretorio_e_ignorado(self):
        # is_directory=True tem que ser filtrado ANTES de checar extensão
        # -- uma pasta criada chamada "algo.flac" (raro, mas possível) não
        # pode ser tratada como arquivo de áudio.
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)
        handler.on_created(_evento("/musicas/pasta.flac", is_directory=True))
        assert fila.qsize() == 0

    def test_on_moved_usa_dest_path_no_src_path(self):
        # Regressao especifica: on_moved tem que enfileirar o destino
        # (nome final apos o rename), nao a origem.
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)
        evento = SimpleNamespace(
            is_directory=False,
            src_path="/tmp/download-parcial-xyz.tmp",
            dest_path="/musicas/Artista/Album/01 - Faixa.flac",
        )
        handler.on_moved(evento)
        assert fila.get_nowait() == "/musicas/Artista/Album"

    def test_on_moved_de_diretorio_e_ignorado(self):
        fila = asyncio.Queue()
        handler = watcher._NewAudioFileHandler(_LoopFalso(), fila)
        evento = SimpleNamespace(
            is_directory=True, src_path="/tmp/a", dest_path="/musicas/pasta.flac"
        )
        handler.on_moved(evento)
        assert fila.qsize() == 0


# --------------------------------------------------------------------
# watch_directory -- validação de entrada
# --------------------------------------------------------------------
class TestWatchDirectoryValidacao:
    async def test_pasta_inexistente_levanta_erro_direto(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            await watcher.watch_directory(str(tmp_path / "nao-existe"))


# --------------------------------------------------------------------
# watch_directory -- loop de debounce, com Observer e inject fakes
# --------------------------------------------------------------------
class TestWatchDirectoryDebounce:
    async def _rodar_e_encerrar(self, task, espera=0.2):
        """Deixa o loop de watch_directory processar por `espera` segundos
        e então cancela -- watch_directory roda pra sempre de propósito
        (Ctrl+C na vida real), então o teste precisa ser quem decide
        quando parar."""
        await asyncio.sleep(espera)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_arquivos_na_mesma_pasta_geram_uma_unica_chamada(
        self, tmp_path, monkeypatch
    ):
        observer_falso = _ObserverFalso()
        monkeypatch.setattr(watcher, "Observer", lambda: observer_falso)

        chamadas = []

        async def inject_falso(directory_path, **kwargs):
            chamadas.append(directory_path)

        monkeypatch.setattr(watcher, "inject_lyrics_retroactively", inject_falso)

        task = asyncio.create_task(
            watcher.watch_directory(str(tmp_path), debounce_seconds=0.05)
        )
        await asyncio.sleep(0.02)  # deixa o Observer ser registrado

        handler = observer_falso.handler
        assert handler is not None

        handler.on_created(_evento(str(tmp_path / "01.flac")))
        await asyncio.sleep(0.01)
        handler.on_created(_evento(str(tmp_path / "02.flac")))  # dentro da janela

        await self._rodar_e_encerrar(task)

        # As duas faixas caem na MESMA pasta (tmp_path) -- uma única
        # chamada de retro-tagging pra essa pasta, não duas.
        assert chamadas == [str(tmp_path)]

    async def test_pastas_diferentes_na_mesma_janela_geram_uma_chamada_cada(
        self, tmp_path, monkeypatch
    ):
        observer_falso = _ObserverFalso()
        monkeypatch.setattr(watcher, "Observer", lambda: observer_falso)

        chamadas = []

        async def inject_falso(directory_path, **kwargs):
            chamadas.append(directory_path)

        monkeypatch.setattr(watcher, "inject_lyrics_retroactively", inject_falso)

        subpasta_a = tmp_path / "AlbumA"
        subpasta_b = tmp_path / "AlbumB"

        task = asyncio.create_task(
            watcher.watch_directory(str(tmp_path), debounce_seconds=0.05)
        )
        await asyncio.sleep(0.02)

        handler = observer_falso.handler
        handler.on_created(_evento(str(subpasta_a / "01.flac")))
        handler.on_created(_evento(str(subpasta_b / "01.flac")))

        await self._rodar_e_encerrar(task)

        # pending_dirs e' um set processado em ordem (sorted) -- as duas
        # pastas do batch, cada uma com sua propria chamada.
        assert sorted(chamadas) == sorted([str(subpasta_a), str(subpasta_b)])

    async def test_falha_numa_pasta_nao_impede_as_outras(self, tmp_path, monkeypatch):
        observer_falso = _ObserverFalso()
        monkeypatch.setattr(watcher, "Observer", lambda: observer_falso)

        subpasta_ok = tmp_path / "AlbumOk"
        subpasta_falha = tmp_path / "AlbumComErro"
        chamadas = []

        async def inject_falso(directory_path, **kwargs):
            chamadas.append(directory_path)
            if directory_path == str(subpasta_falha):
                raise RuntimeError("erro simulado no retro-tagging")

        monkeypatch.setattr(watcher, "inject_lyrics_retroactively", inject_falso)

        task = asyncio.create_task(
            watcher.watch_directory(str(tmp_path), debounce_seconds=0.05)
        )
        await asyncio.sleep(0.02)

        handler = observer_falso.handler
        handler.on_created(_evento(str(subpasta_ok / "01.flac")))
        handler.on_created(_evento(str(subpasta_falha / "01.flac")))

        await self._rodar_e_encerrar(task)

        # As duas pastas foram TENTADAS, mesmo uma delas tendo lançado --
        # o try/except é por pasta, não derruba o loop inteiro.
        assert sorted(chamadas) == sorted([str(subpasta_ok), str(subpasta_falha)])

    async def test_observer_e_parado_ao_cancelar(self, tmp_path, monkeypatch):
        observer_falso = _ObserverFalso()
        monkeypatch.setattr(watcher, "Observer", lambda: observer_falso)
        monkeypatch.setattr(
            watcher,
            "inject_lyrics_retroactively",
            lambda directory_path, **kwargs: asyncio.sleep(0),
        )

        task = asyncio.create_task(
            watcher.watch_directory(str(tmp_path), debounce_seconds=0.05)
        )
        await asyncio.sleep(0.02)
        assert observer_falso.started is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # O `finally` tem que rodar mesmo em cancelamento -- senão o
        # observer (e a thread nativa dele) fica pendurado pra sempre
        # depois que o comando `watch` é interrompido.
        assert observer_falso.stopped is True
        assert observer_falso.joined is True
