# Referência de comandos

> Gerado por `python tools/gerar_referencia.py` a partir de `qobuz_dl/commands.py`.
> **Não edite à mão**: mude o parser e gere de novo.

Para a ajuda completa de um comando no terminal: `qobuz-dl <comando> --help`.

## Índice

- [`interactive`](#interactive)
- [`dl`](#dl)
- [`import-playlist`](#import-playlist)
- [`lucky`](#lucky)
- [`lyrics`](#lyrics)
- [`tags`](#tags)
- [`sync-playlist`](#sync-playlist)
- [`stats`](#stats)
- [`inspect`](#inspect)
- [`auth`](#auth)
- [`retry`](#retry)
- [`sync-favorites`](#sync-favorites)
- [`artists`](#artists)
- [`scan`](#scan)
- [`library`](#library)
- [`organize`](#organize)
- [`doctor`](#doctor)
- [`config`](#config)
- [`duplicates`](#duplicates)
- [`cleanup`](#cleanup)
- [`sync-db`](#sync-db)
- [`watch`](#watch)
- [Flags globais](#flags-globais)
- [Códigos de saída](#códigos-de-saída)

## interactive

Busca interativa: procura faixas/álbuns e escolhe o que baixar na hora.

**Uso:** `qobuz-dl interactive [opções]`

**Apelidos:** `i`, `fun`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `-l`, `--limit` | `N` | limite de resultados da pesquisa (padrão: 20) | `20` |
| `-sd`, `--sort-date` |  | ordena os resultados da busca por data de lançamento, do mais novo ao mais antigo |  |
| `--filter-label` | `GRAVADORA` | Baixa só lançamentos desta gravadora (nome, ID numérico ou URL da gravadora no Qobuz) |  |
| `-d`, `--directory` | `PASTA` | diretório para os downloads (padrão: "~/Qobuz Downloads") | `~/Qobuz Downloads` |
| `--no-lrc-files` |  | não salva letras sincronizadas em arquivos .lrc externos |  |
| `-q`, `--quality` | `QUALIDADE` | "qualidade" do áudio (5, 6, 7, 27) [320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: 6) | `6` |
| `--albums-only` |  | não baixa singles, EPs ou lançamentos de vários artistas (VA) |  |
| `--no-m3u` |  | não cria arquivos .m3u ao baixar playlists |  |
| `--no-fallback` |  | desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida) |  |
| `--no-db` |  | ignora e não consulta o banco de dados |  |
| `-ff`, `--folder-format` | `PADRÃO` | padrão para formatar nomes de pastas, ex: "{album_artist} - {album_title} ({year}) {{{barcode}}}". Use "/" para criar subpastas. As chaves disponíveis estão no fim desta ajuda. |  |
| `-fbff`, `--fallback-folder-format` | `PADRÃO` | padrão alternativo (fallback) para pastas quando o formato principal falhar. |  |
| `-tf`, `--track-format` | `PADRÃO` | padrão para formatar nomes das faixas, ex: "{track_number} - {track_title}". Não pode conter caracteres bloqueados pelo sistema operacional. As chaves disponíveis estão no fim desta ajuda. |  |
| `-s`, `--smart-discography` |  | Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista. |  |
| `--verify-download` |  | decodifica cada faixa com ffmpeg logo após o download terminar para detectar corrupção real no áudio (e não apenas falhas nas tags). |  |
| `--progress-json` |  | emite uma linha JSON para cada evento track-start/track-done no stdout para interfaces externas (scripts, apps). |  |
| `--check-storage` |  | verifica o espaço livre antes de iniciar cada álbum (padrão) |  |
| `--no-check-storage` |  | desativa a verificação automática de espaço livre antes do álbum |  |
| `--delay` | `SEGUNDOS` | Aguarda uma quantidade específica de segundos entre o download das faixas. |  |
| `--playlist-as-albums` |  | Baixa itens de playlist usando a estrutura de pastas do álbum de origem. |  |
| `--no-lyrics` |  | desativa a busca e injeção automática de letras para esta sessão. |  |
| `--no-embed-lyrics` |  | não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt) |  |
| `--lyrics-word-sync` |  | grava letras sincronizadas por palavra (LRC estendido) quando a fonte oferece (BiniLyrics); a maioria dos players só lê por linha |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES |  |
| `--no-multi-tags` |  | desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini) |  |
| `--booklet-only` |  | baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas) |  |
| `--native-lang` |  | não força o idioma para Inglês; obtém metadados no idioma nativo da conta |  |
| `--no-credits` |  | desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum) |  |
| `--with-credits` |  | força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini) |  |
| `--no-album-artist-tag` |  | não adiciona a tag do artista do álbum |  |
| `--no-album-title-tag` |  | não adiciona a tag de título do álbum |  |
| `--no-track-artist-tag` |  | não adiciona a tag do artista da faixa |  |
| `--no-track-title-tag` |  | não adiciona a tag de título da faixa |  |
| `--no-release-date-tag` |  | não adiciona a tag da data de lançamento |  |
| `--no-media-type-tag` |  | não adiciona a tag do tipo de mídia (media type) |  |
| `--no-genre-tag` |  | não adiciona a tag do gênero |  |
| `--no-replaygain-tag` |  | Não adiciona tags de ReplayGain nos arquivos de áudio. |  |
| `--no-album-url-tag` |  | Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio. |  |
| `--no-track-number-tag` |  | não adiciona o número da faixa |  |
| `--no-track-total-tag` |  | não adiciona o total de faixas |  |
| `--no-disc-number-tag` |  | não adiciona o número do disco |  |
| `--no-disc-total-tag` |  | não adiciona o total de discos |  |
| `--no-composer-tag` |  | não adiciona a tag do compositor |  |
| `--no-explicit-tag` |  | não adiciona a tag de conteúdo explícito |  |
| `--musicbrainz` |  | grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, versão, álbum e artista conferem; adiciona cerca de 1 s por faixa |  |
| `--no-copyright-tag` |  | não adiciona a tag de copyright |  |
| `--no-label-tag` |  | não adiciona a tag da gravadora/selo |  |
| `--no-upc-tag` |  | não adiciona a tag do UPC/código de barras |  |
| `--no-isrc-tag` |  | não adiciona a tag do ISRC |  |
| `-e`, `--embed-art` |  | embute a capa no arquivo de áudio |  |
| `--og-cover` |  | baixa a capa na qualidade original |  |
| `--no-cover` |  | não baixa a arte da capa |  |
| `--embedded-art-size` | `50|100|150|300|600|max|org` | tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini) |  |
| `--saved-art-size` | `50|100|150|300|600|max|org` | tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini) |  |
| `--multiple-disc-prefix` | `PREFIXO` | Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini) |  |
| `--multiple-disc-one-dir` |  | armazena todos os lançamentos com discos múltiplos no mesmo diretório base |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--multiple-disc-track-format` | `FORMATO` | formato da faixa para lançamentos com múltiplos discos |  |
| `--max-workers` | `N` | número máximo de downloads paralelos (padrão: 1 -- sequencial) |  |
| `--segment-workers` | `N` | threads usadas internamente no fallback de download segmentado |  |
| `--no-pipeline` |  | desliga o pipeline de download (por padrão, com --max-workers > 1, a vaga de rede é liberada assim que o arquivo baixa e as tags, letras e verificação rodam em paralelo com os próximos downloads) |  |
| `--adaptive-workers` |  | ajusta o número de downloads simultâneos: corta pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads bons (nunca passa de --max-workers) |  |
| `--events` | `ARQUIVO` | grava um evento JSON por linha (progresso de faixas e álbuns) em ARQUIVO; use - para o stderr |  |
| `--on-complete` | `COMANDO` | roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*) |  |
| `--on-error` | `COMANDO` | roda COMANDO quando um álbum/faixa/playlist termina incompleto |  |
| `--on-finish` | `COMANDO` | roda COMANDO uma vez, no fim da sessão de download |  |
| `--webhook` | `URL` | envia os eventos concluídos por POST (JSON) para esta URL |  |
| `--ntfy` | `TOPICO_OU_URL` | notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão |  |
| `--rescan` | `SERVIDORES` | pede ao servidor de música que reescaneie a biblioteca se baixou arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## dl

Baixa por URL de álbum, faixa, artista, gravadora ou playlist do Qobuz, ou um arquivo de texto com uma lista dessas URLs.

**Uso:** `qobuz-dl dl [opções] <URL_OU_ARQUIVO>`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `URL_OU_ARQUIVO` | `URL_OU_ARQUIVO` | uma ou mais URLs (separadas por espaço) ou um arquivo de texto |  |
| `-b`, `--blacklist` | `ARQUIVO` | Caminho para um arquivo de texto contendo palavras-chave para ignorar (blacklist) |  |
| `--filter-label` | `GRAVADORA` | Baixa só lançamentos desta gravadora (nome, ID numérico ou URL da gravadora no Qobuz) |  |
| `--dry-run` |  | Simula o que seria baixado sem fazer o download de fato. |  |
| `--since` | `DATA` | Baixa apenas lançamentos a partir desta data (YYYY-MM-DD ou YYYY). |  |
| `--before` | `DATA` | Baixa apenas lançamentos anteriores a esta data (YYYY-MM-DD ou YYYY). |  |
| `--tag-only` |  | Reaplica as tags nos arquivos já baixados sem baixar novamente. Pula ausentes. |  |
| `--no-sentinel` |  | não grava o arquivo .streamrip.json (sentinela de dedup) nas pastas dos álbuns |  |
| `-d`, `--directory` | `PASTA` | diretório para os downloads (padrão: "~/Qobuz Downloads") | `~/Qobuz Downloads` |
| `--no-lrc-files` |  | não salva letras sincronizadas em arquivos .lrc externos |  |
| `-q`, `--quality` | `QUALIDADE` | "qualidade" do áudio (5, 6, 7, 27) [320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: 6) | `6` |
| `--albums-only` |  | não baixa singles, EPs ou lançamentos de vários artistas (VA) |  |
| `--no-m3u` |  | não cria arquivos .m3u ao baixar playlists |  |
| `--no-fallback` |  | desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida) |  |
| `--no-db` |  | ignora e não consulta o banco de dados |  |
| `-ff`, `--folder-format` | `PADRÃO` | padrão para formatar nomes de pastas, ex: "{album_artist} - {album_title} ({year}) {{{barcode}}}". Use "/" para criar subpastas. As chaves disponíveis estão no fim desta ajuda. |  |
| `-fbff`, `--fallback-folder-format` | `PADRÃO` | padrão alternativo (fallback) para pastas quando o formato principal falhar. |  |
| `-tf`, `--track-format` | `PADRÃO` | padrão para formatar nomes das faixas, ex: "{track_number} - {track_title}". Não pode conter caracteres bloqueados pelo sistema operacional. As chaves disponíveis estão no fim desta ajuda. |  |
| `-s`, `--smart-discography` |  | Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista. |  |
| `--verify-download` |  | decodifica cada faixa com ffmpeg logo após o download terminar para detectar corrupção real no áudio (e não apenas falhas nas tags). |  |
| `--progress-json` |  | emite uma linha JSON para cada evento track-start/track-done no stdout para interfaces externas (scripts, apps). |  |
| `--check-storage` |  | verifica o espaço livre antes de iniciar cada álbum (padrão) |  |
| `--no-check-storage` |  | desativa a verificação automática de espaço livre antes do álbum |  |
| `--delay` | `SEGUNDOS` | Aguarda uma quantidade específica de segundos entre o download das faixas. |  |
| `--playlist-as-albums` |  | Baixa itens de playlist usando a estrutura de pastas do álbum de origem. |  |
| `--no-lyrics` |  | desativa a busca e injeção automática de letras para esta sessão. |  |
| `--no-embed-lyrics` |  | não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt) |  |
| `--lyrics-word-sync` |  | grava letras sincronizadas por palavra (LRC estendido) quando a fonte oferece (BiniLyrics); a maioria dos players só lê por linha |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES |  |
| `--no-multi-tags` |  | desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini) |  |
| `--booklet-only` |  | baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas) |  |
| `--native-lang` |  | não força o idioma para Inglês; obtém metadados no idioma nativo da conta |  |
| `--no-credits` |  | desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum) |  |
| `--with-credits` |  | força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini) |  |
| `--no-album-artist-tag` |  | não adiciona a tag do artista do álbum |  |
| `--no-album-title-tag` |  | não adiciona a tag de título do álbum |  |
| `--no-track-artist-tag` |  | não adiciona a tag do artista da faixa |  |
| `--no-track-title-tag` |  | não adiciona a tag de título da faixa |  |
| `--no-release-date-tag` |  | não adiciona a tag da data de lançamento |  |
| `--no-media-type-tag` |  | não adiciona a tag do tipo de mídia (media type) |  |
| `--no-genre-tag` |  | não adiciona a tag do gênero |  |
| `--no-replaygain-tag` |  | Não adiciona tags de ReplayGain nos arquivos de áudio. |  |
| `--no-album-url-tag` |  | Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio. |  |
| `--no-track-number-tag` |  | não adiciona o número da faixa |  |
| `--no-track-total-tag` |  | não adiciona o total de faixas |  |
| `--no-disc-number-tag` |  | não adiciona o número do disco |  |
| `--no-disc-total-tag` |  | não adiciona o total de discos |  |
| `--no-composer-tag` |  | não adiciona a tag do compositor |  |
| `--no-explicit-tag` |  | não adiciona a tag de conteúdo explícito |  |
| `--musicbrainz` |  | grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, versão, álbum e artista conferem; adiciona cerca de 1 s por faixa |  |
| `--no-copyright-tag` |  | não adiciona a tag de copyright |  |
| `--no-label-tag` |  | não adiciona a tag da gravadora/selo |  |
| `--no-upc-tag` |  | não adiciona a tag do UPC/código de barras |  |
| `--no-isrc-tag` |  | não adiciona a tag do ISRC |  |
| `-e`, `--embed-art` |  | embute a capa no arquivo de áudio |  |
| `--og-cover` |  | baixa a capa na qualidade original |  |
| `--no-cover` |  | não baixa a arte da capa |  |
| `--embedded-art-size` | `50|100|150|300|600|max|org` | tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini) |  |
| `--saved-art-size` | `50|100|150|300|600|max|org` | tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini) |  |
| `--multiple-disc-prefix` | `PREFIXO` | Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini) |  |
| `--multiple-disc-one-dir` |  | armazena todos os lançamentos com discos múltiplos no mesmo diretório base |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--multiple-disc-track-format` | `FORMATO` | formato da faixa para lançamentos com múltiplos discos |  |
| `--max-workers` | `N` | número máximo de downloads paralelos (padrão: 1 -- sequencial) |  |
| `--segment-workers` | `N` | threads usadas internamente no fallback de download segmentado |  |
| `--no-pipeline` |  | desliga o pipeline de download (por padrão, com --max-workers > 1, a vaga de rede é liberada assim que o arquivo baixa e as tags, letras e verificação rodam em paralelo com os próximos downloads) |  |
| `--adaptive-workers` |  | ajusta o número de downloads simultâneos: corta pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads bons (nunca passa de --max-workers) |  |
| `--events` | `ARQUIVO` | grava um evento JSON por linha (progresso de faixas e álbuns) em ARQUIVO; use - para o stderr |  |
| `--on-complete` | `COMANDO` | roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*) |  |
| `--on-error` | `COMANDO` | roda COMANDO quando um álbum/faixa/playlist termina incompleto |  |
| `--on-finish` | `COMANDO` | roda COMANDO uma vez, no fim da sessão de download |  |
| `--webhook` | `URL` | envia os eventos concluídos por POST (JSON) para esta URL |  |
| `--ntfy` | `TOPICO_OU_URL` | notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão |  |
| `--rescan` | `SERVIDORES` | pede ao servidor de música que reescaneie a biblioteca se baixou arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## import-playlist

Importa um arquivo de playlist (TXT, CSV, JSON) de qualquer plataforma para download.

**Uso:** `qobuz-dl import-playlist [opções] <URL_OU_ARQUIVO>`

**Apelidos:** `ip`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `URL_OU_ARQUIVO` | `URL_OU_ARQUIVO` | URL de playlist (Spotify, Deezer, Apple Music) ou caminho para arquivo exportado (.txt, .csv, .json). |  |
| `--name`, `-n` | `NOME` | Nome da pasta local onde as faixas serão salvas (padrão: nome do arquivo). |  |
| `--auto` |  | Aceita automaticamente correspondências duvidosas (>=60%% de similaridade). |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## lucky

Baixa os N primeiros resultados de uma busca no Qobuz, sem passar URL.

**Uso:** `qobuz-dl lucky [opções] <BUSCA>`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `-t`, `--type` | `TIPO` | tipo de item a pesquisar (artist, album, track, playlist) (padrão: album) | `album` |
| `-n`, `--number` | `N` | número de resultados para baixar (padrão: 1) | `1` |
| `BUSCA` | `BUSCA` | termo de busca |  |
| `-d`, `--directory` | `PASTA` | diretório para os downloads (padrão: "~/Qobuz Downloads") | `~/Qobuz Downloads` |
| `--no-lrc-files` |  | não salva letras sincronizadas em arquivos .lrc externos |  |
| `-q`, `--quality` | `QUALIDADE` | "qualidade" do áudio (5, 6, 7, 27) [320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: 6) | `6` |
| `--albums-only` |  | não baixa singles, EPs ou lançamentos de vários artistas (VA) |  |
| `--no-m3u` |  | não cria arquivos .m3u ao baixar playlists |  |
| `--no-fallback` |  | desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida) |  |
| `--no-db` |  | ignora e não consulta o banco de dados |  |
| `-ff`, `--folder-format` | `PADRÃO` | padrão para formatar nomes de pastas, ex: "{album_artist} - {album_title} ({year}) {{{barcode}}}". Use "/" para criar subpastas. As chaves disponíveis estão no fim desta ajuda. |  |
| `-fbff`, `--fallback-folder-format` | `PADRÃO` | padrão alternativo (fallback) para pastas quando o formato principal falhar. |  |
| `-tf`, `--track-format` | `PADRÃO` | padrão para formatar nomes das faixas, ex: "{track_number} - {track_title}". Não pode conter caracteres bloqueados pelo sistema operacional. As chaves disponíveis estão no fim desta ajuda. |  |
| `-s`, `--smart-discography` |  | Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista. |  |
| `--verify-download` |  | decodifica cada faixa com ffmpeg logo após o download terminar para detectar corrupção real no áudio (e não apenas falhas nas tags). |  |
| `--progress-json` |  | emite uma linha JSON para cada evento track-start/track-done no stdout para interfaces externas (scripts, apps). |  |
| `--check-storage` |  | verifica o espaço livre antes de iniciar cada álbum (padrão) |  |
| `--no-check-storage` |  | desativa a verificação automática de espaço livre antes do álbum |  |
| `--delay` | `SEGUNDOS` | Aguarda uma quantidade específica de segundos entre o download das faixas. |  |
| `--playlist-as-albums` |  | Baixa itens de playlist usando a estrutura de pastas do álbum de origem. |  |
| `--no-lyrics` |  | desativa a busca e injeção automática de letras para esta sessão. |  |
| `--no-embed-lyrics` |  | não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt) |  |
| `--lyrics-word-sync` |  | grava letras sincronizadas por palavra (LRC estendido) quando a fonte oferece (BiniLyrics); a maioria dos players só lê por linha |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES |  |
| `--no-multi-tags` |  | desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini) |  |
| `--booklet-only` |  | baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas) |  |
| `--native-lang` |  | não força o idioma para Inglês; obtém metadados no idioma nativo da conta |  |
| `--no-credits` |  | desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum) |  |
| `--with-credits` |  | força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini) |  |
| `--no-album-artist-tag` |  | não adiciona a tag do artista do álbum |  |
| `--no-album-title-tag` |  | não adiciona a tag de título do álbum |  |
| `--no-track-artist-tag` |  | não adiciona a tag do artista da faixa |  |
| `--no-track-title-tag` |  | não adiciona a tag de título da faixa |  |
| `--no-release-date-tag` |  | não adiciona a tag da data de lançamento |  |
| `--no-media-type-tag` |  | não adiciona a tag do tipo de mídia (media type) |  |
| `--no-genre-tag` |  | não adiciona a tag do gênero |  |
| `--no-replaygain-tag` |  | Não adiciona tags de ReplayGain nos arquivos de áudio. |  |
| `--no-album-url-tag` |  | Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio. |  |
| `--no-track-number-tag` |  | não adiciona o número da faixa |  |
| `--no-track-total-tag` |  | não adiciona o total de faixas |  |
| `--no-disc-number-tag` |  | não adiciona o número do disco |  |
| `--no-disc-total-tag` |  | não adiciona o total de discos |  |
| `--no-composer-tag` |  | não adiciona a tag do compositor |  |
| `--no-explicit-tag` |  | não adiciona a tag de conteúdo explícito |  |
| `--musicbrainz` |  | grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, versão, álbum e artista conferem; adiciona cerca de 1 s por faixa |  |
| `--no-copyright-tag` |  | não adiciona a tag de copyright |  |
| `--no-label-tag` |  | não adiciona a tag da gravadora/selo |  |
| `--no-upc-tag` |  | não adiciona a tag do UPC/código de barras |  |
| `--no-isrc-tag` |  | não adiciona a tag do ISRC |  |
| `-e`, `--embed-art` |  | embute a capa no arquivo de áudio |  |
| `--og-cover` |  | baixa a capa na qualidade original |  |
| `--no-cover` |  | não baixa a arte da capa |  |
| `--embedded-art-size` | `50|100|150|300|600|max|org` | tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini) |  |
| `--saved-art-size` | `50|100|150|300|600|max|org` | tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini) |  |
| `--multiple-disc-prefix` | `PREFIXO` | Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini) |  |
| `--multiple-disc-one-dir` |  | armazena todos os lançamentos com discos múltiplos no mesmo diretório base |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--multiple-disc-track-format` | `FORMATO` | formato da faixa para lançamentos com múltiplos discos |  |
| `--max-workers` | `N` | número máximo de downloads paralelos (padrão: 1 -- sequencial) |  |
| `--segment-workers` | `N` | threads usadas internamente no fallback de download segmentado |  |
| `--no-pipeline` |  | desliga o pipeline de download (por padrão, com --max-workers > 1, a vaga de rede é liberada assim que o arquivo baixa e as tags, letras e verificação rodam em paralelo com os próximos downloads) |  |
| `--adaptive-workers` |  | ajusta o número de downloads simultâneos: corta pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads bons (nunca passa de --max-workers) |  |
| `--events` | `ARQUIVO` | grava um evento JSON por linha (progresso de faixas e álbuns) em ARQUIVO; use - para o stderr |  |
| `--on-complete` | `COMANDO` | roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*) |  |
| `--on-error` | `COMANDO` | roda COMANDO quando um álbum/faixa/playlist termina incompleto |  |
| `--on-finish` | `COMANDO` | roda COMANDO uma vez, no fim da sessão de download |  |
| `--webhook` | `URL` | envia os eventos concluídos por POST (JSON) para esta URL |  |
| `--ntfy` | `TOPICO_OU_URL` | notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão |  |
| `--rescan` | `SERVIDORES` | pede ao servidor de música que reescaneie a biblioteca se baixou arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## lyrics

Varre uma pasta já baixada e injeta letras/traduções que estejam faltando.

**Uso:** `qobuz-dl lyrics [opções] [PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | O diretório local contendo os arquivos a serem escaneados (padrão: "~/Qobuz Downloads") |  |
| `--max-workers` | `N` | arquivos processados ao mesmo tempo (padrão: 1 -- sequencial; também lê max_workers do config.ini). O máximo efetivo é 3, porque os provedores de letra limitam requisições |  |
| `--watch` |  | fica observando a PASTA e injeta letras nos arquivos de áudio novos que aparecerem (precisa do extra: pip install 'qobuz-dl-ultra[watch]'); é o mesmo que a flag global --watch |  |
| `--dry-run` |  | SIMULA: consulta as fontes e mostra o que seria feito em cada faixa, sem gravar letra, .lrc nem progresso (também vale para --undo) |  |
| `--resume` |  | continua uma execução interrompida (CTRL+C, queda de rede) e refaz as faixas adiadas ou com falha: pula as que já foram feitas |  |
| `--incremental` |  | só processa arquivos novos ou alterados desde a última execução do lyrics nesta pasta (mais as adiadas ou com falha) |  |
| `--no-undo` |  | não guarda o estado anterior das letras; sem isso cada execução cria um registro para desfazer (são mantidas as 5 últimas) |  |
| `--undo` | `ID` | desfaz uma execução do lyrics (letras embutidas e .lrc; a mais recente, ou a do ID dado; veja --undo-list) e não consulta o Qobuz |  |
| `--undo-list` |  | lista as execuções do lyrics que podem ser desfeitas |  |
| `--undo-force` |  | com --undo: restaura mesmo arquivos alterados depois do lyrics |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## tags

Varre uma pasta já baixada, regrava/corrige as tags de metadados (com ou sem multi-tags) e troca a capa pela da Apple/iTunes quando validada. Escolha: --tags-only (só tags), --covers-only (só capas) ou o padrão (os dois). Use --dry-run para simular, --undo para desfazer, --resume/--incremental em bibliotecas grandes e --report para um relatório da pasta.

**Uso:** `qobuz-dl tags [opções] [PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | O diretório local contendo os arquivos a serem corrigidos (padrão: a pasta do config.ini, atualmente "~/Qobuz Downloads") |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade (sobrescreve config.ini) |  |
| `--no-multi-tags` |  | grava artistas, compositores e gêneros como texto único "A, B" (sobrescreve config.ini) |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--tags-only` |  | só regrava as tags de metadados; NÃO consulta a Apple/iTunes e a capa embutida fica como está |  |
| `--covers-only` |  | só troca a capa embutida (quando a Apple/iTunes tem uma validada e de resolução maior); NÃO regrava nenhuma tag de metadados |  |
| `--force-covers` |  | troca a capa embutida SEMPRE que a Apple/iTunes devolver uma capa validada, mesmo que a atual tenha resolução igual ou maior (ou seja quadrada e a nova não). POR SUA CONTA E RISCO: se a Apple devolver uma capa de outra edição, ela será gravada. Não vale com --tags-only |  |
| `--both` |  | regrava as tags E troca a capa quando validada (é o padrão) |  |
| `--dry-run` |  | SIMULA: mostra o diff real de cada arquivo sem gravar nada na sua pasta (também vale para --undo) |  |
| `--resume` |  | continua uma execução interrompida (CTRL+C, queda de rede): pula os arquivos que ela já tinha terminado |  |
| `--incremental` |  | só processa arquivos novos ou alterados desde a última execução do tags nesta pasta (e neste modo) |  |
| `--no-undo` |  | não guarda os metadados originais; sem isso cada execução cria um registro para desfazer (são mantidas as 5 últimas) |  |
| `--undo` | `ID` | desfaz uma execução do tags (a mais recente, ou a do ID dado; veja --undo-list) e não consulta o Qobuz |  |
| `--undo-list` |  | lista as execuções do tags que podem ser desfeitas |  |
| `--suggest-names` |  | só lê as tags da pasta e sugere artistas com vírgula no nome (como "Tyler, The Creator") para o artists_with_comma do config.ini; não consulta o Qobuz nem grava nada |  |
| `--report` | `ARQUIVO` | só lê a pasta e gera um relatório do que está faltando (sem capa, sem ISRC, ID Qobuz inválido...): HTML, ou CSV se o nome terminar em .csv (padrão: relatorio_biblioteca.html); não consulta o Qobuz nem grava nos arquivos de música |  |
| `--undo-force` |  | com --undo: restaura mesmo arquivos alterados depois do tags |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## sync-playlist

Sincroniza uma pasta local com uma playlist do Qobuz (baixa o que falta, remove o que saiu).

**Uso:** `qobuz-dl sync-playlist [opções] <URL>`

**Apelidos:** `sp`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `URL` |  | URL da playlist do Qobuz (ex. https://play.qobuz.com/playlist/12345) |  |
| `--yes`, `-y` |  | Pula o aviso de confirmação antes de deletar/baixar |  |
| `-d`, `--directory` | `PASTA` | diretório para os downloads (padrão: "~/Qobuz Downloads") | `~/Qobuz Downloads` |
| `--no-lrc-files` |  | não salva letras sincronizadas em arquivos .lrc externos |  |
| `-q`, `--quality` | `QUALIDADE` | "qualidade" do áudio (5, 6, 7, 27) [320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: 6) | `6` |
| `--albums-only` |  | não baixa singles, EPs ou lançamentos de vários artistas (VA) |  |
| `--no-m3u` |  | não cria arquivos .m3u ao baixar playlists |  |
| `--no-fallback` |  | desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida) |  |
| `--no-db` |  | ignora e não consulta o banco de dados |  |
| `-ff`, `--folder-format` | `PADRÃO` | padrão para formatar nomes de pastas, ex: "{album_artist} - {album_title} ({year}) {{{barcode}}}". Use "/" para criar subpastas. As chaves disponíveis estão no fim desta ajuda. |  |
| `-fbff`, `--fallback-folder-format` | `PADRÃO` | padrão alternativo (fallback) para pastas quando o formato principal falhar. |  |
| `-tf`, `--track-format` | `PADRÃO` | padrão para formatar nomes das faixas, ex: "{track_number} - {track_title}". Não pode conter caracteres bloqueados pelo sistema operacional. As chaves disponíveis estão no fim desta ajuda. |  |
| `-s`, `--smart-discography` |  | Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista. |  |
| `--verify-download` |  | decodifica cada faixa com ffmpeg logo após o download terminar para detectar corrupção real no áudio (e não apenas falhas nas tags). |  |
| `--progress-json` |  | emite uma linha JSON para cada evento track-start/track-done no stdout para interfaces externas (scripts, apps). |  |
| `--check-storage` |  | verifica o espaço livre antes de iniciar cada álbum (padrão) |  |
| `--no-check-storage` |  | desativa a verificação automática de espaço livre antes do álbum |  |
| `--delay` | `SEGUNDOS` | Aguarda uma quantidade específica de segundos entre o download das faixas. |  |
| `--playlist-as-albums` |  | Baixa itens de playlist usando a estrutura de pastas do álbum de origem. |  |
| `--no-lyrics` |  | desativa a busca e injeção automática de letras para esta sessão. |  |
| `--no-embed-lyrics` |  | não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt) |  |
| `--lyrics-word-sync` |  | grava letras sincronizadas por palavra (LRC estendido) quando a fonte oferece (BiniLyrics); a maioria dos players só lê por linha |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES |  |
| `--no-multi-tags` |  | desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini) |  |
| `--booklet-only` |  | baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas) |  |
| `--native-lang` |  | não força o idioma para Inglês; obtém metadados no idioma nativo da conta |  |
| `--no-credits` |  | desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum) |  |
| `--with-credits` |  | força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini) |  |
| `--no-album-artist-tag` |  | não adiciona a tag do artista do álbum |  |
| `--no-album-title-tag` |  | não adiciona a tag de título do álbum |  |
| `--no-track-artist-tag` |  | não adiciona a tag do artista da faixa |  |
| `--no-track-title-tag` |  | não adiciona a tag de título da faixa |  |
| `--no-release-date-tag` |  | não adiciona a tag da data de lançamento |  |
| `--no-media-type-tag` |  | não adiciona a tag do tipo de mídia (media type) |  |
| `--no-genre-tag` |  | não adiciona a tag do gênero |  |
| `--no-replaygain-tag` |  | Não adiciona tags de ReplayGain nos arquivos de áudio. |  |
| `--no-album-url-tag` |  | Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio. |  |
| `--no-track-number-tag` |  | não adiciona o número da faixa |  |
| `--no-track-total-tag` |  | não adiciona o total de faixas |  |
| `--no-disc-number-tag` |  | não adiciona o número do disco |  |
| `--no-disc-total-tag` |  | não adiciona o total de discos |  |
| `--no-composer-tag` |  | não adiciona a tag do compositor |  |
| `--no-explicit-tag` |  | não adiciona a tag de conteúdo explícito |  |
| `--musicbrainz` |  | grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, versão, álbum e artista conferem; adiciona cerca de 1 s por faixa |  |
| `--no-copyright-tag` |  | não adiciona a tag de copyright |  |
| `--no-label-tag` |  | não adiciona a tag da gravadora/selo |  |
| `--no-upc-tag` |  | não adiciona a tag do UPC/código de barras |  |
| `--no-isrc-tag` |  | não adiciona a tag do ISRC |  |
| `-e`, `--embed-art` |  | embute a capa no arquivo de áudio |  |
| `--og-cover` |  | baixa a capa na qualidade original |  |
| `--no-cover` |  | não baixa a arte da capa |  |
| `--embedded-art-size` | `50|100|150|300|600|max|org` | tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini) |  |
| `--saved-art-size` | `50|100|150|300|600|max|org` | tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini) |  |
| `--multiple-disc-prefix` | `PREFIXO` | Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini) |  |
| `--multiple-disc-one-dir` |  | armazena todos os lançamentos com discos múltiplos no mesmo diretório base |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--multiple-disc-track-format` | `FORMATO` | formato da faixa para lançamentos com múltiplos discos |  |
| `--max-workers` | `N` | número máximo de downloads paralelos (padrão: 1 -- sequencial) |  |
| `--segment-workers` | `N` | threads usadas internamente no fallback de download segmentado |  |
| `--no-pipeline` |  | desliga o pipeline de download (por padrão, com --max-workers > 1, a vaga de rede é liberada assim que o arquivo baixa e as tags, letras e verificação rodam em paralelo com os próximos downloads) |  |
| `--adaptive-workers` |  | ajusta o número de downloads simultâneos: corta pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads bons (nunca passa de --max-workers) |  |
| `--events` | `ARQUIVO` | grava um evento JSON por linha (progresso de faixas e álbuns) em ARQUIVO; use - para o stderr |  |
| `--on-complete` | `COMANDO` | roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*) |  |
| `--on-error` | `COMANDO` | roda COMANDO quando um álbum/faixa/playlist termina incompleto |  |
| `--on-finish` | `COMANDO` | roda COMANDO uma vez, no fim da sessão de download |  |
| `--webhook` | `URL` | envia os eventos concluídos por POST (JSON) para esta URL |  |
| `--ntfy` | `TOPICO_OU_URL` | notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão |  |
| `--rescan` | `SERVIDORES` | pede ao servidor de música que reescaneie a biblioteca se baixou arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## stats

Mostra estatísticas detalhadas sobre sua biblioteca e downloads efetuados.

**Uso:** `qobuz-dl stats [--artistas] [--json]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `--artistas` |  | Mostra a lista completa de todos os artistas únicos baixados. |  |
| `--json` |  | saída em JSON |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## inspect

Mostra tudo de uma faixa local: dados técnicos, tags por assunto, capas, letras, diagnóstico e autenticidade (ou resume uma pasta).

**Uso:** `qobuz-dl inspect [opções] [ARQUIVO_OU_PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `ARQUIVO_OU_PASTA` | `ARQUIVO_OU_PASTA` | arquivo de áudio (inspeciona direto) ou pasta (onde começar a navegação; padrão: a pasta do config.ini; sem ela, detecta pelo dispositivo) |  |
| `--resumo` |  | resume todas as faixas da pasta (formato, qualidade, capa, problemas) sem abrir o navegador |  |
| `--auditar` |  | com PASTA: confere se o nome da pasta e o comentário de cada arquivo batem com a qualidade real do áudio (só leitura; implica --resumo) |  |
| `--json` |  | imprime só o JSON do relatório (para scripts); com PASTA, o resumo dela |  |
| `--completo` |  | também decodifica o áudio inteiro (integridade) e mede volume e pico com o ffmpeg |  |
| `--sem-espectro` |  | pula a análise espectral de autenticidade (bem mais rápido, ideal no celular) |  |
| `--comparar` |  | compara o ARQUIVO com o Qobuz e mostra o que o `tags` mudaria (simulação; precisa de login) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## auth

Mostra a conta e a assinatura (perfil, plano, limites; --json traz o bruto) ou atualiza e-mail e token com -l. Também responde por user, account, profile, me e info.

**Uso:** `qobuz-dl auth [opções]`

**Apelidos:** `login`, `user`, `account`, `profile`, `me`, `info`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `-l`, `--login` |  | Atualizar e-mail e token de autenticação. |  |
| `--oauth` |  | Autenticar pela página oficial do Qobuz e capturar o token automaticamente. |  |
| `--oauth-no-browser` |  | Usar OAuth sem callback local: exibe o link e aceita a URL de retorno colada (iPad/EC2). |  |
| `--json` |  | Exibe o JSON bruto retornado pelo endpoint user/get |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## retry

Repete as faixas puladas ou que falharam em álbuns já baixados, na mesma pasta (usa o .report.json de cada álbum).

**Uso:** `qobuz-dl retry [PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | pasta com os álbuns (procura em todas as subpastas); padrão: a pasta de downloads do config.ini | `~/Qobuz Downloads` |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## sync-favorites

Sincroniza os favoritos da conta com o catálogo local (novos/removidos) e baixa o que falta.

**Uso:** `qobuz-dl sync-favorites [opções]`

**Apelidos:** `sf`

**Saída diferente de 0:** 1 se algum download falhou

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `--download-new` |  | baixa os álbuns favoritados desde a última sincronização |  |
| `--download-missing` |  | baixa TODOS os favoritos que ainda não estão completos no disco |  |
| `--limit` | `N` | máximo de álbuns baixados por execução |  |
| `--every` | `MIN` | repete a sincronização a cada MIN minutos (modo contínuo/NAS) |  |
| `--dry-run` |  | só mostra o diff (novos/removidos); não grava nem baixa |  |
| `--yes`, `-y` |  | não pede confirmação antes de baixar |  |
| `--no-sentinel` |  | não grava o arquivo .streamrip.json nas pastas dos álbuns |  |
| `-d`, `--directory` | `PASTA` | diretório para os downloads (padrão: "~/Qobuz Downloads") | `~/Qobuz Downloads` |
| `--no-lrc-files` |  | não salva letras sincronizadas em arquivos .lrc externos |  |
| `-q`, `--quality` | `QUALIDADE` | "qualidade" do áudio (5, 6, 7, 27) [320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: 6) | `6` |
| `--albums-only` |  | não baixa singles, EPs ou lançamentos de vários artistas (VA) |  |
| `--no-m3u` |  | não cria arquivos .m3u ao baixar playlists |  |
| `--no-fallback` |  | desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida) |  |
| `--no-db` |  | ignora e não consulta o banco de dados |  |
| `-ff`, `--folder-format` | `PADRÃO` | padrão para formatar nomes de pastas, ex: "{album_artist} - {album_title} ({year}) {{{barcode}}}". Use "/" para criar subpastas. As chaves disponíveis estão no fim desta ajuda. |  |
| `-fbff`, `--fallback-folder-format` | `PADRÃO` | padrão alternativo (fallback) para pastas quando o formato principal falhar. |  |
| `-tf`, `--track-format` | `PADRÃO` | padrão para formatar nomes das faixas, ex: "{track_number} - {track_title}". Não pode conter caracteres bloqueados pelo sistema operacional. As chaves disponíveis estão no fim desta ajuda. |  |
| `-s`, `--smart-discography` |  | Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista. |  |
| `--verify-download` |  | decodifica cada faixa com ffmpeg logo após o download terminar para detectar corrupção real no áudio (e não apenas falhas nas tags). |  |
| `--progress-json` |  | emite uma linha JSON para cada evento track-start/track-done no stdout para interfaces externas (scripts, apps). |  |
| `--check-storage` |  | verifica o espaço livre antes de iniciar cada álbum (padrão) |  |
| `--no-check-storage` |  | desativa a verificação automática de espaço livre antes do álbum |  |
| `--delay` | `SEGUNDOS` | Aguarda uma quantidade específica de segundos entre o download das faixas. |  |
| `--playlist-as-albums` |  | Baixa itens de playlist usando a estrutura de pastas do álbum de origem. |  |
| `--no-lyrics` |  | desativa a busca e injeção automática de letras para esta sessão. |  |
| `--no-embed-lyrics` |  | não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt) |  |
| `--lyrics-word-sync` |  | grava letras sincronizadas por palavra (LRC estendido) quando a fonte oferece (BiniLyrics); a maioria dos players só lê por linha |  |
| `--multi-tags` |  | grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES |  |
| `--no-multi-tags` |  | desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini) |  |
| `--booklet-only` |  | baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas) |  |
| `--native-lang` |  | não força o idioma para Inglês; obtém metadados no idioma nativo da conta |  |
| `--no-credits` |  | desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum) |  |
| `--with-credits` |  | força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini) |  |
| `--no-album-artist-tag` |  | não adiciona a tag do artista do álbum |  |
| `--no-album-title-tag` |  | não adiciona a tag de título do álbum |  |
| `--no-track-artist-tag` |  | não adiciona a tag do artista da faixa |  |
| `--no-track-title-tag` |  | não adiciona a tag de título da faixa |  |
| `--no-release-date-tag` |  | não adiciona a tag da data de lançamento |  |
| `--no-media-type-tag` |  | não adiciona a tag do tipo de mídia (media type) |  |
| `--no-genre-tag` |  | não adiciona a tag do gênero |  |
| `--no-replaygain-tag` |  | Não adiciona tags de ReplayGain nos arquivos de áudio. |  |
| `--no-album-url-tag` |  | Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio. |  |
| `--no-track-number-tag` |  | não adiciona o número da faixa |  |
| `--no-track-total-tag` |  | não adiciona o total de faixas |  |
| `--no-disc-number-tag` |  | não adiciona o número do disco |  |
| `--no-disc-total-tag` |  | não adiciona o total de discos |  |
| `--no-composer-tag` |  | não adiciona a tag do compositor |  |
| `--no-explicit-tag` |  | não adiciona a tag de conteúdo explícito |  |
| `--musicbrainz` |  | grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, versão, álbum e artista conferem; adiciona cerca de 1 s por faixa |  |
| `--no-copyright-tag` |  | não adiciona a tag de copyright |  |
| `--no-label-tag` |  | não adiciona a tag da gravadora/selo |  |
| `--no-upc-tag` |  | não adiciona a tag do UPC/código de barras |  |
| `--no-isrc-tag` |  | não adiciona a tag do ISRC |  |
| `-e`, `--embed-art` |  | embute a capa no arquivo de áudio |  |
| `--og-cover` |  | baixa a capa na qualidade original |  |
| `--no-cover` |  | não baixa a arte da capa |  |
| `--embedded-art-size` | `50|100|150|300|600|max|org` | tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini) |  |
| `--saved-art-size` | `50|100|150|300|600|max|org` | tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini) |  |
| `--multiple-disc-prefix` | `PREFIXO` | Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini) |  |
| `--multiple-disc-one-dir` |  | armazena todos os lançamentos com discos múltiplos no mesmo diretório base |  |
| `--continuous-track-numbers` |  | grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini) |  |
| `--multiple-disc-track-format` | `FORMATO` | formato da faixa para lançamentos com múltiplos discos |  |
| `--max-workers` | `N` | número máximo de downloads paralelos (padrão: 1 -- sequencial) |  |
| `--segment-workers` | `N` | threads usadas internamente no fallback de download segmentado |  |
| `--no-pipeline` |  | desliga o pipeline de download (por padrão, com --max-workers > 1, a vaga de rede é liberada assim que o arquivo baixa e as tags, letras e verificação rodam em paralelo com os próximos downloads) |  |
| `--adaptive-workers` |  | ajusta o número de downloads simultâneos: corta pela metade quando há falha de rede ou bloqueio e sobe 1 a cada 4 downloads bons (nunca passa de --max-workers) |  |
| `--events` | `ARQUIVO` | grava um evento JSON por linha (progresso de faixas e álbuns) em ARQUIVO; use - para o stderr |  |
| `--on-complete` | `COMANDO` | roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*) |  |
| `--on-error` | `COMANDO` | roda COMANDO quando um álbum/faixa/playlist termina incompleto |  |
| `--on-finish` | `COMANDO` | roda COMANDO uma vez, no fim da sessão de download |  |
| `--webhook` | `URL` | envia os eventos concluídos por POST (JSON) para esta URL |  |
| `--ntfy` | `TOPICO_OU_URL` | notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão |  |
| `--rescan` | `SERVIDORES` | pede ao servidor de música que reescaneie a biblioteca se baixou arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## artists

Lista artistas seguidos em ordem alfabética ou sorteia um para você conferir no Qobuz e marcar como revisado.

**Uso:** `qobuz-dl artists [--modo alfabetica|sorteio] [--sem-sincronizar] [--json] [--criar-playlist-vazia --id-artista ID [--abrir-perfil]] [--sincronizar-checklist [ARQUIVO] [--espelhar] [--atualizar-seguidos]]`

**Apelidos:** `artist-review`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `--modo` | `alfabetica|sorteio` | lista todos em ordem alfabética ou sorteia um artista (padrão: alfabetica) | `alfabetica` |
| `--sem-sincronizar` |  | usa o catálogo local sem consultar favoritos e discografias no Qobuz |  |
| `--json` |  | emite os artistas como JSON (id, nome e URL), sem abrir a interface interativa nem consultar as discografias |  |
| `--sincronizar-checklist` | `ARQUIVO` | traz para o banco o que está marcado na checklist do Apple Notas; sem ARQUIVO usa tracklist-artists.txt junto ao config.ini. Só marca por padrão e não exige login |  |
| `--espelhar` |  | com --sincronizar-checklist: a Nota manda; reabre quem está conferido no banco mas desmarcado na Nota |  |
| `--atualizar-seguidos` |  | com --sincronizar-checklist: antes, atualiza a lista de seguidos pela API (exige login) |  |
| `--criar-playlist-vazia` |  | cria uma playlist privada e vazia com o nome do artista em maiúsculas; requer --id-artista |  |
| `--id-artista` | `ID` | ID Qobuz do artista para --criar-playlist-vazia |  |
| `--abrir-perfil` |  | abre o perfil do artista depois de criar a playlist vazia |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## scan

Casa sua pasta de música com o catálogo (tag de ID, UPC, nome, fuzzy) e grava sentinelas.

**Uso:** `qobuz-dl scan [opções] [PASTA]`

**Apelidos:** `library-scan`

**Saída diferente de 0:** 1 se a pasta não existe

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | pasta a varrer (padrão: diretório de downloads) |  |
| `--dry-run` |  | classifica e reporta sem gravar nada |  |
| `--no-sentinel` |  | não grava .streamrip.json (use em montagens somente-leitura) |  |
| `--no-adopt` |  | não adiciona ao catálogo pastas com tag de ID que não estão nos favoritos |  |
| `--no-review` |  | não pergunta sobre os casos duvidosos |  |
| `--rescan` |  | reexamina também pastas que já têm sentinela |  |
| `--max-depth` | `N` | profundidade máxima de pastas (padrão: 4) | `4` |
| `--fuzzy-threshold` | `0-1` | similaridade mínima para sugerir um candidato (padrão: 0.85) | `0.85` |
| `--json` | `ARQUIVO` | salva o relatório completo em JSON |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## library

Status e manutenção do catálogo local (missing, history, reconcile, reset-stuck).

**Uso:** `qobuz-dl library [ação] [opções]`

**Apelidos:** `lib`

**Saída diferente de 0:** 1 em reconcile com sentinelas inválidas; 2 para ação desconhecida ou --json em reset-stuck/unmark

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `ação` | `ação` | uma de: status, missing, list, history, reconcile, reset-stuck, unmark | `status` |
| `ALVO` | `ALVO` | pasta (reconcile) ou ID do álbum (unmark) |  |
| `--limit` | `N` | limita a listagem |  |
| `--fix` |  | reconcile: devolve a not_downloaded o que sumiu do disco |  |
| `--dry-run` |  | reconcile: só reporta |  |
| `--json` |  | saída em JSON (status, missing, list, history e reconcile) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## organize

Reaplica folder_format/track_format aos arquivos existentes, sem rede; use --dry-run para revisar e --undo para restaurar a organização anterior.

**Uso:** `qobuz-dl organize [opções] [PASTA]`

**Apelidos:** `rename`

**Saída diferente de 0:** 1 se houve conflito ou erro ao mover arquivos

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | biblioteca a reorganizar (padrão: directory do config.ini) |  |
| `-ff`, `--folder-format` | `FORMATO` | formato de pasta a aplicar (padrão: folder_format do config.ini) |  |
| `-tf`, `--track-format` | `FORMATO` | formato de faixa a aplicar (padrão: track_format do config.ini) |  |
| `--dry-run` |  | prévia somente leitura; não move arquivos nem cria journal |  |
| `--show-files` |  | com --dry-run, lista cada arquivo e os caminhos relativos |  |
| `--include-playlists` |  | inclui a pasta Playlist (por padrão, ela é preservada) |  |
| `--no-undo` |  | não registra movimentos para --undo (não recomendado) |  |
| `--undo` | `ID` | restaura a última execução ou a execução informada no journal |  |
| `--undo-list` |  | lista as execuções registradas disponíveis para desfazer |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## doctor

Diagnostica ambiente, config, bancos e sentinelas (somente leitura).

**Uso:** `qobuz-dl doctor [--fix] [--json]`

**Apelidos:** `check`

**Saída diferente de 0:** 1 se alguma verificação deu FAIL (avisos não contam)

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `--fix` |  | corrige o que é seguro (permissão do config, pasta de downloads) |  |
| `--json` |  | saída em JSON |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## config

Mostra e altera o config.ini sem abrir um editor: show, path, get CHAVE, set CHAVE VALOR (preserva comentários) e reset. Credenciais ficam ocultas.

**Uso:** `qobuz-dl config [show|path|get|set|reset] [CHAVE] [VALOR]`

**Apelidos:** `cfg`

**Saída diferente de 0:** 1 se o config.ini não existe ou está vazio, a opção não existe, o valor é recusado ou o arquivo não pôde ser gravado; 2 se falta CHAVE ou VALOR

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `action` | `show|path|get|set|reset` | o que fazer (padrão: show) | `show` |
| `CHAVE` | `CHAVE` | opção |  |
| `VALOR` | `VALOR` | novo valor (set) |  |
| `--nova` |  | com 'set': cria a opção se ela ainda não existir |  |
| `-y`, `--yes` |  | com 'reset': não pede confirmação |  |
| `--json` |  | saída em JSON |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## duplicates

Acha faixas repetidas e diz qual cópia fica; --mover leva as sobras para _duplicatas (sem apagar) e --desfazer devolve.

**Uso:** `qobuz-dl duplicates [PASTA] [--mover | --desfazer] [--json]`

**Apelidos:** `dups`

**Saída diferente de 0:** 1 se a pasta não existe, se o --desfazer deixou algo sem devolver ou se algum arquivo não pôde ser movido

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | biblioteca a verificar (padrão: directory do config.ini) |  |
| `--mover` |  | move as cópias a mais para PASTA/_duplicatas |  |
| `--desfazer` |  | devolve ao lugar o que o último --mover tirou |  |
| `--json` |  | saída em JSON |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## cleanup

Limpa temporários de download abandonados e pastas vazias. Simula por padrão; --apagar remove de verdade.

**Uso:** `qobuz-dl cleanup [PASTA] [--apagar] [--idade HORAS]`

**Apelidos:** `clean`

**Saída diferente de 0:** 1 se a pasta não existe

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | biblioteca a limpar (padrão: directory do config.ini) |  |
| `--apagar` |  | remove de verdade (sem isto, só simula) |  |
| `--idade` | `HORAS` | idade mínima dos temporários, em horas (padrão: 24) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## sync-db

Restaura no banco de downloads os IDs das faixas que já estão numa pasta (o mesmo que --sync-db).

**Uso:** `qobuz-dl sync-db [PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | pasta a varrer (padrão: a do config.ini) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## watch

Observa a pasta e injeta letras em arquivos novos (o mesmo que --watch).

**Uso:** `qobuz-dl watch [PASTA]`

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `PASTA` | `PASTA` | pasta a observar (padrão: a do config.ini) |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes |  |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## Flags globais

Valem antes do comando (`qobuz-dl -v stats`) e, as de saída, também depois dele.

| Opção | Valor | O que faz | Padrão |
| :--- | :--- | :--- | :--- |
| `-r`, `--reset` |  | cria/reseta o arquivo de configuração |  |
| `-p`, `--purge` |  | apaga o banco de dados de IDs baixados |  |
| `--sync-db` | `PASTA` | escaneia um diretório local para restaurar IDs ausentes do Qobuz no banco de dados |  |
| `--find-duplicates` | `PASTA` | escaneia o diretório local em busca de faixas duplicadas por impressão digital de áudio (Chromaprint/AcoustID), não apenas tags |  |
| `--watch` | `PASTA` | observa o diretório local e executa a retro-marcação (injeção de letras) automaticamente sempre que novos arquivos de áudio aparecerem |  |
| `-sc`, `--show-config` |  | mostra a configuração atual |  |
| `-v`, `--verbose` |  | mostra mensagens de diagnóstico (nível DEBUG) |  |
| `--quiet` |  | mostra apenas avisos e erros (bom para cron/scripts) |  |
| `--no-color` |  | desliga as cores ANSI (respeita também a variável NO_COLOR) |  |
| `--ui` | `auto|compact|comfortable` | modo visual: auto detecta a largura; compact usa cartões para celular/iPad; comfortable privilegia detalhes | `auto` |
| `--log-level` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` | controle fino do logger (sobrepõe -v/--quiet). Útil pra isolar ruído de rede/retry sem ligar todo o DEBUG do programa |  |

## Códigos de saída

| Código | Significado |
| :--- | :--- |
| `0` | deu certo |
| `1` | falhou, no todo ou em parte (ou Ctrl+C) |
| `2` | uso errado: ação desconhecida ou argumento que falta |
