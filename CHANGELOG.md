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
- Pipeline de download: com `--max-workers` maior que 1, a vaga de rede é liberada assim
  que o arquivo baixa, e tags, capa, letras e verificação seguem em paralelo com os
  próximos downloads (`pipeline.py`). O teto de downloads simultâneos continua sendo o
  `--max-workers`. `--no-pipeline` (ou `pipeline = false` no `config.ini`) desliga.
- `--adaptive-workers` (ou `adaptive_workers = true` no `config.ini`): corta os downloads
  simultâneos pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads
  bons, sem passar do `--max-workers`.
- Favoritos: nova opção "Minhas playlists (criadas)", separada das playlists favoritadas
  (que seguem em "Playlists"). A separação usa o dono da playlist e a lista é paginada
  até o fim.
- `Client.get_artist_tracks`: faixas do artista (`extra=tracks`), em vez das páginas de
  álbuns.
- `--continuous-track-numbers` (`dl` e `tags`) e `continuous_track_numbers` no
  `config.ini`: em álbuns com vários discos, grava `TRACKNUMBER`/`TRCK` seguidos de 1 a N
  (ex.: 1 a 11) e `TRACKTOTAL` = N, para o player mostrar a numeração contínua.
  `DISCNUMBER`, `DISCTOTAL` e `TPOS` continuam gravados, então o player ainda divide por
  disco. Vale para álbum, faixa avulsa, playlist e `tags` (faixa avulsa e playlist
  consultam o álbum uma vez; se a consulta falhar, a faixa fica com a numeração por
  disco). Sem a flag nada muda: o padrão segue com a numeração por disco, e as pastas
  `CD 01`/`CD 02` e o nome dos arquivos são sempre por disco.
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
- Resumo de fim de download (álbum, playlist, playlist importada e lote de faixas) ganhou a
  linha `Tempo total` e fica em uma única caixa; `Já existentes` aparece só quando há
  arquivos já baixados.
- Cabeçalhos de `PLAYLIST`, `PLAYLIST IMPORTADA` e `LOTE DE FAIXAS` não mostram a linha
  `QUALIDADE` (continua em `ÁLBUM` e `FAIXA`).
- Nome de pasta padrão usa `{quality_tag}`: `[FLAC 24]` para FLAC e só `[MP3]` para MP3
  (o Qobuz informa 16 bits para MP3 e `[MP3 16]` enganava).
- Comentário técnico (COMMENT) grava a qualidade real do arquivo (bits e taxa no FLAC,
  kbps no MP3) e, quando ela difere do máximo do catálogo, uma linha `Máx. no Qobuz`.
  Os links do álbum e da faixa saem e entra `Trk ID`.
- COMMENT do MP3 passa a ter uma linha por item, como no FLAC. MP3 antigos, em linha única
  separada por ` | `, continuam sendo lidos e mantêm o formato ao serem atualizados.
- Listagem de playlists do usuário envia `type` e `offset` (entram antes da assinatura da
  requisição) e pagina pelas linhas brutas, sem perder itens quando o filtro de dono
  descarta alguns.
- Changelog automático (`tools/gerar_changelog.py` + workflow `changelog.yml`): em vez de
  só listar os títulos dos commits, a release agora descreve o que mudou, lendo o diff de
  cada commit com o **GitHub Models** (IA gratuita do GitHub, via `GITHUB_TOKEN` com
  `models: read`), com resumo da versão e lista dos arquivos alterados. Sem token, com
  `--sem-ia` ou se a IA falhar, volta ao modo antigo pelos títulos. Novas opções:
  `--sem-ia`, `--exigir-ia`, `--modelo` (ou a variável `CHANGELOG_MODELO` do repositório)
  e `--limite-prompt`. Regerar a release ainda em rascunho agora atualiza as notas.
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
- Download em paralelo (a-Shell, `--max-workers 5`): o terminal não trava mais numa
  faixa enquanto a pasta já mostra tudo baixado. A busca de letras rodava com o
  `print_lock` preso e, com uma fonte em limite de requisições (429), dormia até 60 s por
  tentativa; as outras faixas e o loop (que imprimem pelo mesmo lock) ficavam parados.
  Agora a busca não segura o lock, cada fonte tem um orçamento de espera (15 s; cooldown
  acima de 5 s é pulado e a próxima fonte é tentada, com aviso `limite de requisições em
  X, usando outra fonte`), as letras usam um pool próprio de até 3 threads (não disputam
  vaga com tags e verificação) e as linhas de cada faixa saem juntas. O token do
  Musixmatch é pedido uma vez só. `run_bounded` aninhado com o mesmo limite não pode mais
  travar. O comando `lyrics` mantém o comportamento de esperar o cooldown.
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
