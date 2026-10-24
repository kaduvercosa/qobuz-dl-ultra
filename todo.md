# qobuz-dl-ultra — pendências

> Mantido junto com o `SKILL.md` (ver §11 de lá). A GUI web foi **removida** do projeto em 2026-10-06; este arquivo antes descrevia a GUI e foi reescrito.

## Para decidir / resolver

- [ ] **Subir a cobertura mínima:** `pyproject.toml` (`fail_under`), `codecov.yml` (alvo do projeto) e o `--cov-fail-under` do `tests.yml` agora usam o mesmo valor, **60%** (o que o CI já exigia; era 75% nos outros dois). Falta medir a cobertura real num PC (`python -m pytest --cov=qobuz_dl --cov-report=term`; as partes puladas no a-Shell, como Pillow/cryptography/prompt_toolkit, só rodam lá) e subir os **três** juntos para o valor medido, arredondado para baixo.
- [ ] **Baseline do mypy:** o `mypy-baseline.txt` ainda não está versionado; enquanto não existir, o CI gera um novo a cada execução (então não detecta erro novo). O workflow `.github/workflows/mypy-baseline.yml` já gera e commita o arquivo: faça commit dele e rode uma vez em Actions → *Gerar mypy-baseline* → Run workflow (depois dê `git pull`). Só então marque este item.
- [ ] **Quebra de arquivos grandes:** `core.py`, `downloader.py` e `utils.py` (~2–3 mil linhas cada). Seguir extraindo só o que já tem teste (`download_utils.py`, `interactive_ui.py`, `stats_view.py` já foram). Primeiros candidatos em `utils.py`: o bloco de **capas Apple/iTunes** (`apple_release_kind`, `termo_de_busca`, `faixas_equivalentes`, `get_apple_hq_cover`, `verify_albums_by_tracklist`…) e o de **comparação de títulos** (`extrair_essencia`, `extrair_titulo_comparavel`, `marcadores_versao`…). Mover para módulos novos e deixar re-exportado em `utils` para não quebrar imports nem testes.

- [ ] **Telas no estilo da tela inicial:** `stats`, `inspect` e `library` ainda usam o estilo antigo (`====` e `rótulo: valor`); `doctor`, a tela inicial e a ajuda dos comandos já usam painéis. Converter mexe nos testes que checam o texto dessas telas (`test_stats_view`, testes do inspector, `test_library_cmd`).
- [ ] **Conferir em ~40 colunas:** tela de busca do modo interativo e relatório de progresso do `tags`; `retro_tags.py` tem linhas com `ljust` — se preencherem nomes de artista, usar `ui.pad_cells` (emoji/japonês passam da largura).
- [ ] **`report.html` a cada faixa:** o relatório (JSON + HTML) ainda é regravado inteiro por faixa, agora em thread. Se álbuns/playlists enormes ficarem lentos, agrupar as gravações (no máximo 1 por segundo + uma no final).
- [ ] **Operações pequenas que ainda rodam no loop** (de propósito, microssegundos): `os.rename`/`os.remove`/`os.makedirs` do downloader e do `retro_tags`, `open()` do arquivo parcial em `http_download._attempt`, `input()` dos assistentes interativos. `shutil.rmtree` também fica síncrono: em thread ele trava no a-Shell (copyfile/copy2 em thread funcionam). Descobrir o motivo num PC/CI antes de tentar de novo. Se algum dia travar em armazenamento lento (iCloud/a-Shell), mover para `to_thread`; o teste-guarda já avisa se algo pesado entrar.

## Concluído recentemente

- [x] `except` que só fazia `pass`/`continue` agora registra com `logger.debug`/`warning` quando esconderia falha real (token que não saiu do `config.ini` após migrar para o keyring, journal ilegível, tags/capas ilegíveis etc.); os 38 casos intencionais ganharam um comentário com o motivo e `tests/unit/test_excecoes_silenciosas.py` falha se aparecer um novo sem comentário.
- [x] Testes em ordem aleatória (`pytest-randomly` no extra `dev`; o CI fixa `--randomly-seed` = número da execução e grava a semente no log). Para reproduzir: `pytest --randomly-seed=N`; para desligar: `pytest -p no:randomly`.
- [x] `CHANGELOG.md` criado (formato Keep a Changelog); atualize a seção *Não lançado* a cada mudança.
- [x] README: bloco `usage:` antigo trocado pela descrição da ajuda em painéis (`qobuz-dl <comando> -h`).
- [x] Varreduras de `sync`, `sync_playlist`, `favorites_sync`, `retro_tags`, cópias do `inspector`/`downloader` e esperas fora do event loop + teste-guarda por AST.
- [x] Letras retroativas em paralelo (`lyrics --max-workers N`, teto 4), relatório sempre na ordem dos arquivos.
- [x] Relatório pós-download e letras retroativas fora do event loop (leitura/gravação, tags e `fetch_and_inject` em thread) + teste de regressão.

- [x] README: `—` e aspas curvas dentro de comandos trocados por `--` e aspas retas (copiar e colar voltou a funcionar).
- [x] Removido o `qobuz_dl/pyproject.toml` duplicado (cópia antiga, divergia em `Pillow`).
- [x] `tests/unit/test_core_interactive_branches-2026-09-28.py` renomeado para `test_core_interactive_branches.py` (o arquivo com esse nome estava vazio).
