# qobuz-dl-ultra — pendências

> Mantido junto com o `SKILL.md` (ver §11 de lá). A GUI web foi **removida** do projeto em 2026-10-06; este arquivo antes descrevia a GUI e foi reescrito.

## Para decidir / resolver

- [ ] **Subir a cobertura mínima:** `pyproject.toml` (`fail_under`), `codecov.yml` (alvo do projeto) e o `--cov-fail-under` do `tests.yml` agora usam o mesmo valor, **60%** (o que o CI já exigia; era 75% nos outros dois). Falta medir a cobertura real num PC (`python -m pytest --cov=qobuz_dl --cov-report=term`; as partes puladas no a-Shell, como Pillow/cryptography/prompt_toolkit, só rodam lá) e subir os **três** juntos para o valor medido, arredondado para baixo.
- [ ] **Baseline do mypy:** o `mypy-baseline.txt` ainda não está versionado; enquanto não existir, o CI gera um novo a cada execução (então não detecta erro novo). O workflow `.github/workflows/mypy-baseline.yml` já gera e commita o arquivo: faça commit dele e rode uma vez em Actions → *Gerar mypy-baseline* → Run workflow (depois dê `git pull`). Só então marque este item.
- [ ] **Quebra de arquivos grandes e funções gigantes:** `core.py` (~3,6 mil linhas), `downloader.py` (~3,7 mil) e `cli.py` (~2,7 mil). O bloco de capas Apple e a comparação de títulos já saíram de `utils.py` (agora ~1 mil linhas; ver `apple_cover.py`). Funções a quebrar primeiro: `core._tui_select` (~1,3 mil linhas, 32 funções internas e `nonlocal`; virar uma classe com um `dataclass` de estado), `apple_cover.get_apple_hq_cover` (~1 mil), `cli.async_main`, `retro_tags.retag_directory`, `downloader.download_release` e `_download_and_tag_impl`. Seguir extraindo só o que já tem teste e deixar re-exportado para não quebrar imports.

- [ ] **Conferir em ~40 colunas** (a largura agora vem toda de `ui.raw_width()`: teste com `COLUMNS=40`): tela de busca do modo interativo e relatório de progresso do `tags`; `retro_tags.py` tem linhas com `ljust` — se preencherem nomes de artista, usar `ui.pad_cells` (emoji/japonês passam da largura).
- [ ] **`report.html` a cada faixa:** o relatório (JSON + HTML) ainda é regravado inteiro por faixa, agora em thread. Se álbuns/playlists enormes ficarem lentos, agrupar as gravações (no máximo 1 por segundo + uma no final).
- [ ] **Operações pequenas que ainda rodam no loop** (de propósito, microssegundos): `os.rename`/`os.remove`/`os.makedirs` do downloader e do `retro_tags`, `open()` do arquivo parcial em `http_download._attempt`, `input()` dos assistentes interativos. `shutil.rmtree` também fica síncrono: em thread ele trava no a-Shell (copyfile/copy2 em thread funcionam). Descobrir o motivo num PC/CI antes de tentar de novo. Se algum dia travar em armazenamento lento (iCloud/a-Shell), mover para `to_thread`; o teste-guarda já avisa se algo pesado entrar.
- [ ] **Estado global e `except Exception`:** 12 `global` (pools em `executors.py`, `_MB_SEM`, `_history`, cores em `cli.py`) e ~210 `except Exception`. Reduzir os que cercam regra de negócio; deixar só os de UI cosmética. Revisão de 2026-11-02: dos 214 `except Exception`, 70 não relançam nem registram; os de `qopy.search_*` foram corrigidos, o resto são fallbacks de leitura/cosmética (metadata, apple_cover coleta em `network_errors`, cli) e podem ficar. Um objeto de contexto trocaria os globais.
- [ ] **Nome confuso:** em `cli.py` (leitura do `config.ini`) o campo `password` acaba guardando o *token* (`cfg.token = ini_token or ini_password`). Separar os dois conceitos, com migração do `config.ini` antigo.
- [ ] **Anotações de tipo:** ~560 funções sem retorno anotado; ao anotar um módulo, acrescentá-lo nos `overrides` do mypy.
- [ ] **`C901` desligado e `max-complexity = 55`:** reativar a regra e baixar o limite aos poucos, junto com a quebra das funções gigantes.

## Concluído recentemente

- [x] `tags` usa a mesma caixa "ÁLBUM" (`ui.header`) do `lyrics`; a largura dela vem de `ui.ALBUM_BOX_COLS`.
- [x] Largura do terminal unificada em `ui.raw_width()`/`ui.width()`/`ui.report_width()`; helpers de bloco rotulado (`ui.labeled_lines`, `ui.wrap_indented`) compartilhados por `retro_tagger.py` e `retro_tags.py`. Restam `color.py` (o `ui` importa o `color`, então não dá para importar de volta) e as duas chamadas que precisam da altura (`core.py` e `artist_checklist.py`): criar `ui.raw_height()`.
- [x] Testes duplicados removidos (`regression/test_dependencias.py`, `unit/test_qopy_respx.py`); `httpx` redundante saiu do `pyproject.toml`; CI com delimitador aleatório no resumo de falhas.

- [x] `except` que só fazia `pass`/`continue` agora registra com `logger.debug`/`warning` quando esconderia falha real (token que não saiu do `config.ini` após migrar para o keyring, journal ilegível, tags/capas ilegíveis etc.); os casos intencionais ganharam um comentário com o motivo (as duas rotinas best-effort de `download_utils.py` estão documentadas na docstring e listadas por nome em `tests/unit/test_excecoes_silenciosas.py`), e esse teste falha se aparecer um novo sem comentário.
- [x] Testes em ordem aleatória (`pytest-randomly` no extra `dev`; o CI fixa `--randomly-seed` = número da execução e grava a semente no log). Para reproduzir: `pytest --randomly-seed=N`; para desligar: `pytest -p no:randomly`.
- [x] `CHANGELOG.md` criado (formato Keep a Changelog); atualize a seção *Não lançado* a cada mudança.
- [x] Telas no estilo da tela inicial: `stats`, `inspect` e `library` (scan, status, reconcile, sync-favorites) agora usam painéis (`ui.Painel`); as listagens `library list/missing/history` ficaram em uma linha por álbum de propósito.
- [x] README: bloco `usage:` antigo trocado pela descrição da ajuda em painéis (`qobuz-dl <comando> -h`).
- [x] Varreduras de `sync`, `sync_playlist`, `favorites_sync`, `retro_tags`, cópias do `inspector`/`downloader` e esperas fora do event loop + teste-guarda por AST.
- [x] Letras retroativas em paralelo (`lyrics --max-workers N`, teto 4), relatório sempre na ordem dos arquivos.
- [x] Relatório pós-download e letras retroativas fora do event loop (leitura/gravação, tags e `fetch_and_inject` em thread) + teste de regressão.

- [x] README: `—` e aspas curvas dentro de comandos trocados por `--` e aspas retas (copiar e colar voltou a funcionar).
- [x] Removido o `qobuz_dl/pyproject.toml` duplicado (cópia antiga, divergia em `Pillow`).
- [x] `tests/unit/test_core_interactive_branches-2026-09-28.py` renomeado para `test_core_interactive_branches.py` (o arquivo com esse nome estava vazio).
