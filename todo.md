# qobuz-dl-ultra — pendências

> Mantido junto com o `SKILL.md` (ver §11 de lá). A GUI web foi **removida** do projeto em 2026-10-06; este arquivo antes descrevia a GUI e foi reescrito.

## Para decidir / resolver

- [ ] **Subir a cobertura mínima:** `pyproject.toml` (`fail_under`), `codecov.yml` (alvo do projeto) e o `--cov-fail-under` do `tests.yml` agora usam o mesmo valor, **60%** (o que o CI já exigia; era 75% nos outros dois). Falta medir a cobertura real num PC (`python -m pytest --cov=qobuz_dl --cov-report=term`; as partes puladas no a-Shell, como Pillow/cryptography/prompt_toolkit, só rodam lá) e subir os **três** juntos para o valor medido, arredondado para baixo.
- [ ] **Baseline do mypy:** o `mypy-baseline.txt` ainda não está versionado; enquanto não existir, o CI gera um novo a cada execução (então não detecta erro novo). O workflow `.github/workflows/mypy-baseline.yml` já gera e commita o arquivo: faça commit dele e rode uma vez em Actions → *Gerar mypy-baseline* → Run workflow (depois dê `git pull`). Só então marque este item.
- [ ] **Quebra de arquivos grandes e funções gigantes:** `core.py` (~3,6 mil linhas), `downloader.py` (~3,7 mil) e `cli.py` (~2,7 mil). O bloco de capas Apple e a comparação de títulos já saíram de `utils.py` (agora ~1 mil linhas; ver `apple_cover.py`). Funções a quebrar primeiro: `core._tui_select` (~1,3 mil linhas, 32 funções internas e `nonlocal`; virar uma classe com um `dataclass` de estado), `apple_cover.get_apple_hq_cover` (~1 mil), `cli.async_main`, `retro_tags.retag_directory`, `downloader.download_release` e `_download_and_tag_impl`. Seguir extraindo só o que já tem teste e deixar re-exportado para não quebrar imports.

- [ ] **Operações pequenas que ainda rodam no loop** (de propósito, microssegundos): `os.rename`/`os.remove`/`os.makedirs` do downloader e do `retro_tags`, `open()` do arquivo parcial em `http_download._attempt`, `input()` dos assistentes interativos. `shutil.rmtree` também fica síncrono: em thread ele trava no a-Shell (copyfile/copy2 em thread funcionam). Descobrir o motivo num PC/CI antes de tentar de novo. Se algum dia travar em armazenamento lento (iCloud/a-Shell), mover para `to_thread`; o teste-guarda já avisa se algo pesado entrar.
- [ ] **Estado global:** `global` fora do `ui.py` (pools em `executors.py`, `_MB_SEM`, `_history`, cores em `cli.py`; o estado do `ui.py` já virou `ui.estado`). Um objeto de contexto trocaria os globais. Os `except Exception` foram revisados (ver *Concluído*): não há mais o que fazer em massa.
- [ ] **Anotações de tipo:** `mypy .` está limpo e o `mypy-baseline.txt` está vazio (qualquer erro novo derruba o CI). Módulos 100% anotados ficam em `[[tool.mypy.overrides]]` (44 hoje). Faltam ~700 funções, sobretudo em `ui.py` (106), `core.py` (59), `downloader.py` (58), `qopy.py` (48), `inspector.py` (43), `metadata.py` (41), `artist_checklist.py` (38), `cli.py` (37). Fluxo: anotar um módulo, rodar `python3 -m mypy .`, corrigir o que aparecer e só então acrescentá-lo aos `overrides`.
- [ ] **`C901` desligado e `max-complexity = 55`:** reativar a regra e baixar o limite aos poucos, junto com a quebra das funções gigantes.

## Concluído recentemente

- [x] `password` e token separados no `config.ini` (`cli._separar_senha_e_token`): um valor com mais de 60 caracteres no campo `password` é tratado como token (formato antigo), migrado para o keyring (ou `auth_token`) e o campo é esvaziado; `cfg.password` guarda só senha de verdade. Testes em `tests/unit/test_senha_e_token.py`.
- [x] 40 colunas: o relatório do `tags` e o do `lyrics` cabem na tela (`ui.wrap_ansi_line`, `ui.emit_wrapped`, `ui.resumo_linhas`; uma palavra maior que a linha, como um caminho, não é partida: vai inteira para uma linha só dela; piso de `ui.report_width()` baixou de 60 para 32 e a coluna de rótulos do resumo passou de 26 para 28 no `tags`, porque "Capas Apple não encontradas" tem 27 caracteres e deslocava o `:`). Testes em `tests/unit/test_largura_40_colunas.py`. **Falta rodar** no seu ambiente `tests/unit/test_core_tui_40_colunas.py` (lista interativa com prompt_toolkit real; é pulado onde ele não existe e no iOS, `QOBUZ_TUI_REAL=1` força).
- [x] `report.html`: já é gerado só na finalização do álbum (`finalize_report`), uma vez por álbum, não por faixa (por faixa só o `.report.json`). Nada a agrupar.
- [x] `except Exception` revisados (212 handlers): nenhum `except:` puro, todo `except BaseException` repropaga (`tests/unit/test_guarda_excecoes.py`), os 5 que só ignoram têm comentário (guarda já existente) e os ~40 restantes são fallbacks de leitura/formatação.
- [x] `.github/workflows/qualidade.yml` criado (`vulture`, não bloqueia); o `pip-audit` já era o job `auditoria-dependencias` do `tests.yml`.

- [x] Estado do terminal em `ui.estado` (`EstadoUI`) em vez de 7 globais em `ui.py`; `ui.redefinir_estado()` e `ui.estado_temporario()` para testes.
- [x] `--json` uniforme (`ui.emit_json`), `doctor --fix`, `docs/COMANDOS.md` gerado do parser (`tools/gerar_referencia.py`) e códigos de saída documentados.
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
- [x] `tests/unit/test_core_interativo_favoritos_e_artista-2026-09-28.py` renomeado para `test_core_interativo_favoritos_e_artista.py` (o arquivo com esse nome estava vazio).
