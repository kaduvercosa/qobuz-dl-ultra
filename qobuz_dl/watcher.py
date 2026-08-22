"""
Monitora uma pasta e roda o retro-tagging (retro_tagger.py) automaticamente
quando arquivos de áudio novos aparecem -- por exemplo, baixados por outra
ferramenta, sincronizados de outro dispositivo, copiados manualmente, etc.
Transforma o retro-tagging de "rodar na mão sempre que lembrar" pra "roda
sozinho".

Usa a lib watchdog (observer nativo do SO -- inotify no Linux, FSEvents no
macOS, ReadDirectoryChangesW no Windows; cai pra polling em sistemas sem
suporte nativo, o que cobre o caso do iSH/a-Shell no iOS).
"""
import asyncio
import logging
import os

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from qobuz_dl.color import SUCCESS as GREEN, WARNING as YELLOW, INFO as CYAN, OFF
from qobuz_dl.retro_tagger import inject_lyrics_retroactively

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".flac", ".mp3")


class _NewAudioFileHandler(FileSystemEventHandler):
    """
    Roda na thread interna do watchdog -- NAO na thread do event loop
    asyncio. Por isso essa classe so' enfileira o caminho da pasta afetada;
    todo o trabalho de verdade (retro-tagging, chamadas de rede) acontece
    do lado async, em watch_directory() abaixo.
    """

    def __init__(self, loop, queue):
        super().__init__()
        self._loop = loop
        self._queue = queue

    def _enqueue(self, path):
        if not path.lower().endswith(AUDIO_EXTENSIONS):
            return
        # call_soon_threadsafe e' obrigatorio aqui: a callback do watchdog
        # roda numa thread separada da do asyncio, entao um
        # queue.put_nowait() direto NAO seria thread-safe.
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait, os.path.dirname(path)
        )

    def on_created(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event):
        # Cobre o padrao comum de download: arquivo criado com nome
        # temporario/parcial (on_created dispara, mas normalmente nao bate
        # AUDIO_EXTENSIONS) e depois renomeado pro nome final -- e' esse
        # evento de rename que importa de verdade.
        if not event.is_directory:
            self._enqueue(event.dest_path)


async def watch_directory(
    directory, client=None, genius_token=None, settings=None, debounce_seconds=15
):
    """
    Fica rodando indefinidamente (Ctrl+C pra sair) observando 'directory'
    recursivamente. Quando arquivos de audio novos aparecem, espera
    'debounce_seconds' sem NENHUM evento novo antes de disparar o
    retro-tagging -- isso agrupa um album inteiro chegando (que cria varios
    arquivos em sequencia rapida) numa unica passada, em vez de disparar
    uma vez por arquivo.

    O retro-tagging roda so' na(s) subpasta(s) especifica(s) onde os
    arquivos novos apareceram (nao relanca um scan da biblioteca inteira a
    cada evento), pra ficar leve mesmo em bibliotecas grandes.

    Args:
        directory (str): Pasta raiz a observar recursivamente.
        client: Cliente Qobuz autenticado (opcional -- sem ele, so o
            fallback Genius/letras ja embutidas funciona).
        genius_token (str, optional): Token do Genius pra fallback.
        settings (QobuzDLSettings, optional): Config atual.
        debounce_seconds (int): Janela de silencio antes de processar.
    """
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"Pasta não encontrada: {directory}")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    handler = _NewAudioFileHandler(loop, queue)
    observer = Observer()
    observer.schedule(handler, directory, recursive=True)
    observer.start()

    logger.info(
        f"{GREEN}[*] Observando '{directory}' -- retro-tagging automático "
        f"ativado. Ctrl+C para parar.{OFF}"
    )

    pending_dirs = set()
    try:
        while True:
            # Espera o primeiro evento -- bloqueia indefinidamente sem
            # consumir CPU à toa enquanto nada está acontecendo.
            first_dir = await queue.get()
            pending_dirs.add(first_dir)

            # Debounce: continua acumulando pastas afetadas enquanto
            # eventos novos chegarem dentro da janela; só processa quando
            # a fila ficar quieta por 'debounce_seconds'.
            while True:
                try:
                    next_dir = await asyncio.wait_for(
                        queue.get(), timeout=debounce_seconds
                    )
                    pending_dirs.add(next_dir)
                except asyncio.TimeoutError:
                    break

            logger.info(
                f"{CYAN}[*] Atividade detectada em {len(pending_dirs)} "
                f"pasta(s) -- rodando retro-tagging...{OFF}"
            )
            for target_dir in sorted(pending_dirs):
                try:
                    await inject_lyrics_retroactively(
                        directory_path=target_dir,
                        client=client,
                        genius_token=genius_token,
                        settings=settings,
                    )
                except Exception as e:
                    logger.error(
                        f"{YELLOW}[!] Falha no retro-tagging automático de "
                        f"'{target_dir}': {e}{OFF}"
                    )
            pending_dirs.clear()
    finally:
        observer.stop()
        observer.join()
