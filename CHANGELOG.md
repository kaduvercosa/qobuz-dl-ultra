# Changelog

Todas as mudanças relevantes do qobuz-dl-ultra ficam aqui, no formato
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e com versões no
padrão do projeto (`qobuz_dl/__init__.py`).

> As seções de **2.6.1 a 2.6.5.4** foram reconstruídas a partir dos assuntos dos
> commits e das tags, então são um resumo; os detalhes estão em
> `git log vANTERIOR..vNOVA`. O que vem antes da 2.6.1 não foi listado: veja as tags.

Ao mudar algo, acrescente uma linha em **Não lançado** na mesma hora; ao
publicar, renomeie a seção para a nova versão e a data.

## [Não lançado]

### Adicionado
- Aviso de versão nova na tela inicial: uma linha discreta (`▲ Atualização disponível:
  v2.6.6 → v2.7.0 · pip install -U qobuz-dl-ultra`) que aparece em toda abertura até a
  pessoa atualizar. Compara a versão instalada com a última do **PyPI**, sempre ao vivo e
  sem cache (`update_check.py`); `QOBUZ_DL_NO_UPDATE_CHECK=1` desliga a checagem.
- `qobuz_notes_sync`: `--nota ARQUIVO` usa a Nota do iOS como fonte da verdade
  (só entram os artistas cujo `[QOBUZ_ID:...]` ainda não está nela);
  `--imprimir linhas` devolve só as linhas a acrescentar; grava
  `ultimo-resumo.txt`; acha o JSON mesmo com banners de login na saída.
- Metadados: aliases de coletânea (`various_artists_aliases`) e `TRACKTOTAL` por disco.
- Testes em ordem aleatória (`pytest-randomly` no extra `dev`). O CI fixa a semente
  (`--randomly-seed`) e a grava no log; para reproduzir use `pytest --randomly-seed=N`.
- `tests/unit/test_excecoes_silenciosas.py`: falha se um `except` que só faz
  `pass`/`continue` não tiver um comentário com o motivo.
- Workflow `mypy-baseline.yml` para gerar o `mypy-baseline.txt` uma única vez.

### Alterado
- Álbuns com vários discos: `TRACKNUMBER`/`TRCK` passam a ir de 1 a N e `TRACKTOTAL`
  é o total do álbum (ex.: 11), para o player mostrar 1 a 11. `DISCNUMBER`, `DISCTOTAL`
  e `TPOS` continuam gravados, então o player ainda divide por disco. Vale para álbum,
  faixa avulsa, playlist e para o `tags`: faixa avulsa e playlist buscam a lista de
  faixas do álbum (uma vez por álbum); se a busca falhar, a faixa fica com a numeração
  por disco. As pastas `CD 01`/`CD 02` e o nome dos arquivos (por disco) continuam
  iguais. Substitui o `TRACKTOTAL` por disco.
- `inspect` sem pasta no comando abre na `directory` do `config.ini` (se existir);
  sem ela, mantém a detecção pela pasta de música do dispositivo. Os demais comandos
  com pasta (`tags`, `lyrics`, `scan`, `library reconcile`, `--sync-db`,
  `--find-duplicates`, `--watch`, `dl`) já usavam essa pasta como padrão.
- Erros antes engolidos agora são registrados: `warning` quando o usuário precisa
  saber (token que não saiu do `config.ini` depois de migrar para o keyring;
  journal do `tags` ilegível) e `debug` com traceback nos demais casos
  (tags, capas, playlists e ffmpeg ilegíveis, WAL indisponível etc.).
  Os 38 silêncios intencionais ganharam um comentário com o motivo.
- Cobertura mínima unificada em **60%** no `pyproject.toml`, no `codecov.yml` e
  no `tests.yml` (antes: 75% nos dois primeiros e 60% no CI).

### Corrigido
- `test_cria_playlist_vazia_com_nome_em_maiusculas` falhava só na suíte inteira:
  o cliente falso não tinha `get_user_playlists` e um handler de log deixado por
  outro teste jogava o aviso na saída capturada. O cliente falso foi completado e
  o `conftest.py` ganhou um fixture que desfaz o `ui.install_logging()` de cada teste.

## [2.6.5.5] - 2026-10-23

### Corrigido
- `artists --sincronizar-checklist --atualizar-seguidos` aceita uma Nota sem
  itens: todos os artistas seguidos voltam em `new_followed_missing`, e nada é
  marcado nem desmarcado.

## [2.6.5.4] - 2026-10-21 e [2.6.5.3] - 2026-10-21

### Alterado
- Ajustes na organização da biblioteca e no modo interativo.

## [2.6.5.2] - 2026-10-18

### Adicionado
- `lyrics --max-workers N`: letras retroativas em paralelo (teto de 4), com o
  relatório sempre na ordem dos arquivos.

### Corrigido
- O cancelamento de um download propaga `CancelledError` e espera as tarefas terminarem.
- Correções em `retro_tags` e nas tags do MusicBrainz.

### Desempenho
- Descriptografia de segmentos, checagens de caminho e o I/O pesado restante
  saíram do event loop; um teste-guarda por AST avisa se algo pesado voltar.

## [2.6.5.1] - 2026-10-17

### Adicionado
- `inspect`: dados técnicos, tags por assunto, capas, letras e diagnóstico.
- `tags` com `--dry-run`, desfazer, retomar e relatório; capas e melhorias de uso.
- Playlists salvas em `Playlist/` e remoção de faixas duplicadas ao baixar o álbum.
- Função do artista e tabela interativa aprimorada.
- Ajuda dos comandos em painéis, com rótulos em português.

### Alterado
- A capa da Apple passou a respeitar a edição do álbum (Deluxe x padrão).

## [2.6.5] - 2026-10-13 a [2.6.2.1] - 2026-10-08

### Alterado
- Várias rodadas de ajustes no modo interativo, nas tags e na organização.
- HTTP/2 habilitado (`httpx[http2]`), Docker e testes atualizados; tratamento de
  exceções do `http_download` reforçado.

### Removido
- A GUI web (removida em 2026-10-06).

## [2.6.1] - 2026-09-26

### Corrigido
- Sincronização de favoritos: limite de downloads negativo vale como ilimitado, o
  número de álbuns baixados entra no histórico do modo interativo e o limite em
  `pick_download_targets` é convertido com segurança para inteiro ou `None`.

[Não lançado]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5.5...HEAD
[2.6.5.5]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5.4...v2.6.5.5
[2.6.5.4]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5.3...v2.6.5.4
[2.6.5.3]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5.2...v2.6.5.3
[2.6.5.2]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5.1...v2.6.5.2
[2.6.5.1]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.5...v2.6.5.1
[2.6.5]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.4.1...v2.6.5
[2.6.2.1]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.1.4...v2.6.2.1
[2.6.1]: https://github.com/kaduvercosa/qobuz-dl-ultra/compare/v2.6.0...v2.6.1
