> Guia completo do `qobuz-dl-ultra` — o que o programa tem, como funciona e como usar

# Guia do qobuz-dl-ultra

Referência única e completa do projeto. Todo o conteúdo aqui foi extraído do código-fonte da versão 2.4.8.2 (não de documentação antiga), executando os comandos e lendo os módulos.

## Índice

1. [O que é](#1-o-que-é)
2. [Instalação](#2-instalação)
3. [Primeira configuração](#3-primeira-configuração)
4. [Onde ficam os arquivos](#4-onde-ficam-os-arquivos)
5. [Os comandos](#5-os-comandos)
6. [Operações globais de manutenção](#6-operações-globais-de-manutenção)
7. [Qualidade de áudio](#7-qualidade-de-áudio)
8. [Formatação de pastas e nomes](#8-formatação-de-pastas-e-nomes)
9. [Metadados e tags](#9-metadados-e-tags)
10. [Letras e tradução](#10-letras-e-tradução)
11. [Capas e encartes](#11-capas-e-encartes)
12. [Controle da saída no terminal](#12-controle-da-saída-no-terminal)
13. [Variáveis de ambiente](#13-variáveis-de-ambiente)
14. [O config.ini completo](#14-o-configini-completo)
15. [O banco de dados](#15-o-banco-de-dados)
16. [Automação e integração](#16-automação-e-integração)
17. [Arquitetura interna](#17-arquitetura-interna)
18. [Solução de problemas](#18-solução-de-problemas)
19. [Receitas prontas](#19-receitas-prontas)

—

## 1. O que é

`qobuz-dl-ultra` é um downloader de linha de comando para o Qobuz focado em áudio lossless e Hi-Res, com ênfase em **metadados de qualidade de arquivista**: ReplayGain, campos clássicos (compositor, maestro, obra), ISRC, UPC, MBIDs do MusicBrainz, letras sincronizadas com tradução bilíngue e encartes digitais.

Diferente de um downloader simples, ele mantém um **banco de dados local** do que já foi baixado, sabe reconstruir esse banco a partir dos arquivos em disco, detecta duplicatas por impressão digital acústica e pode vigiar uma pasta aplicando tags automaticamente.

O pacote se chama `qobuz-dl-ultra` no PyPI e instala **dois comandos equivalentes**: `qobuz-dl` e o atalho `qdl`.

Requer **Python 3.9 ou superior**. É preciso ter uma **conta Qobuz com assinatura ativa** — a ferramenta não contorna nada, apenas usa a API oficial com as suas credenciais.

### Dependências externas (fora do pip)

Duas funcionalidades dependem de binários do sistema. Ambas são **verificadas uma vez na inicialização**, e a falta aparece como aviso acionável — não como erro por arquivo:

| Binário | Necessário para | Sem ele |
|—|—|—|
| `ffmpeg` | `—verify-download` e operações de remux | a verificação de integridade é pulada com aviso, o download continua |
| `fpcalc` (Chromaprint) | `—find-duplicates` por impressão digital acústica | cai para comparação por metadados; só é cobrado se você usar `—find-duplicates` |

A busca inclui `$APPDIR/bin`, onde o a-Shell traz ffmpeg nativo — no iPad normalmente nada precisa ser instalado.

—

## 2. Instalação

Requer **Python 3.10 ou mais novo**.

```bash
pip install qobuz-dl-ultra
```

Isso instala o núcleo: 17 dependências, todas Python puro exceto o `cryptography`
(que é indispensável — a autenticação da API do Qobuz é feita com HKDF + AES).
Todos os comandos de download, busca, metadados, letras via Qobuz, `radar` e
`stats` funcionam com essa instalação.

### Extras

Recursos que dependem de pacotes compilados ou pesados ficam em extras. Em
desktop, o mais simples é pegar tudo:

```bash
pip install “qobuz-dl-ultra[all]”
```

| Extra | Instala | O que habilita |
|—|—|—|
| `speed` | `rapidfuzz`, `brotli` | comparação de nomes mais rápida e descompressão Brotli |
| `covers` | `Pillow` | recompactar capa que passe do limite de 16 MB do FLAC |
| `watch` | `watchdog` | `—watch` (vigiar uma pasta) |
| `duplicates` | `pyacoustid` | `—find-duplicates` por impressão digital acústica |
| `genius` | `lyricsgenius` | buscar letras no Genius quando o Qobuz não tem |
| `all` | todos acima | — |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-httpx`, `ruff` | rodar a suíte de testes |

Combinando só o que você quer:

```bash
pip install “qobuz-dl-ultra[speed,covers]”
```

Sem um extra, o recurso correspondente avisa o que instalar em vez de dar erro:

```
[!] —watch precisa do extra ‘watch’.
    Instale com: pip install ‘qobuz-dl-ultra[watch]’
```

A comparação de nomes é a única que **não** desaparece sem o extra: sem
`rapidfuzz` ela usa o `difflib` da biblioteca padrão — mais lento, mesmo
resultado. Confira qual está ativo com:

```bash
python -c “from qobuz_dl.fuzzy import nome_do_motor; print(nome_do_motor())”
```

Direto do repositório, para acompanhar o desenvolvimento:

```bash
git clone https://github.com/kaduvercosa/qobuz-dl-ultra
cd qobuz-dl-ultra
pip install -e “.[dev]”
```

Instalando os binários externos:

```bash
# Debian/Ubuntu
sudo apt install ffmpeg libchromaprint-tools

# macOS
brew install ffmpeg chromaprint

# Windows (winget)
winget install ffmpeg
```

Há também um `Dockerfile` no repositório.

### iOS / iPadOS

O projeto tem suporte explícito a [a-Shell](https://holzschu.github.io/a-Shell_iOS/). A detecção é automática: quando `”Containers/Data/Application”` aparece no `$HOME`, a configuração é redirecionada para `~/Documents`, que é a única pasta persistente e visível no app Arquivos. Você também pode forçar o caminho com a variável `QOBUZ_DL_IOS_HOME`.

Nesse ambiente, `—find-duplicates` cai automaticamente para o modo de comparação por metadados (artista + título + duração), já que não há `fpcalc`.

**Instale sem extras no iPad:**

```bash
pip install qobuz-dl-ultra
```

O a-Shell só instala pacotes Python puro — a documentação dele diz que o compilador C ainda não produz bibliotecas dinâmicas. Por isso o núcleo foi montado para não ter nenhum pacote compilado além do `cryptography`, que o a-Shell já embute pronto. Não rode `pip install —upgrade cryptography` lá: o wheel do PyPI falha ao carregar e quebra o ambiente ([issue #797](https://github.com/holzschu/a-shell/issues/797)).

Os extras `speed`, `covers`, `watch` e `duplicates` não instalam no a-Shell — e não precisam. O programa detecta a ausência e segue funcionando.

—

## 3. Primeira configuração

```bash
qobuz-dl -r
```

Isso abre um assistente interativo que cria o `config.ini`. Ele pergunta, em ordem:

1. **Cor de destaque** — um seletor visual; o valor RGB fica salvo em `accent_color` e passa a colorir toda a interface.
2. **E-mail da conta Qobuz**.
3. **Token de autenticação do navegador** — veja abaixo.
4. **Usar o keyring do sistema?** — se sim, o token é criptografado no gerenciador de credenciais do SO; se não, fica em texto plano no `config.ini`.
5. **Baixar e injetar letras automaticamente?**
6. **Token da API do Genius** (opcional, só se letras estiverem ligadas).
7. **Pasta de download**.
8. **Formato de pasta**.
9. **Qualidade padrão** (o assistente sugere 27).

### Sobre o token: por que não é senha

A API do Qobuz **bloqueou o login por senha direta para aplicativos de terceiros**. Por isso o assistente pede um token extraído do navegador:

1. Abra e faça login em [play.qobuz.com](https://play.qobuz.com).
2. `F12` → aba **Application** (ou **Armazenamento**) → **Local Storage** → o domínio do Qobuz.
3. Localize a entrada de usuário e copie o valor de `token`.
4. Cole no assistente.

O token expira eventualmente. Quando expirar, rode `qobuz-dl -r` novamente — ou, se preferir não refazer tudo, edite `auth_token` no `config.ini` (e deixe `disable_keyring = true` para que ele seja lido de lá).

### Conferindo

```bash
qobuz-dl -sc      # mostra a configuração atual
```

### Uma armadilha do keyring

Se você escolher usar o keyring em **Linux headless, NAS ou Docker**, ele pode falhar silenciosamente por não haver um agente de credenciais rodando. O sintoma é erro de autenticação mesmo com token válido. Nesses ambientes, responda “yes” para desativar o keyring, ou defina `disable_keyring = true`.

—

## 4. Onde ficam os arquivos

A resolução de caminhos segue esta ordem de precedência:

| Prioridade | Condição | Caminho da configuração |
|—|—|—|
| 1 | `QOBUZ_DL_IOS_HOME` definida | `$QOBUZ_DL_IOS_HOME/qobuz-dl/` |
| 2 | `CONFIG_DIR` definida | `$CONFIG_DIR/qobuz-dl/` |
| 3 | Windows | `%APPDATA%\qobuz-dl\` |
| 4 | iOS / a-Shell detectado | `~/Documents/qobuz-dl/` |
| 5 | Padrão (Linux, macOS) | `~/.config/qobuz-dl/` |

Nessa pasta ficam dois arquivos:

- **`config.ini`** — todas as suas preferências.
- **`qobuz_dl.db`** — o banco SQLite com o histórico de downloads.

A variável `CONFIG_DIR` é especialmente útil para manter **perfis separados** (ver [Receitas](#19-receitas-prontas)).

—

## 5. Os comandos

```
qobuz-dl <comando> [opções]
```

| Comando | Aliases | O que faz |
|—|—|—|
| `dl` | — | baixa a partir de URLs do Qobuz |
| `interactive` | `i`, `fun` | busca e seleção interativa |
| `lucky` | — | baixa os primeiros resultados de uma busca |
| `import-playlist` | `ip` | importa playlist de outra plataforma |
| `sync-playlist` | `sp` | espelha uma pasta local com uma playlist do Qobuz |
| `lyrics` | — | injeta letras retroativamente em arquivos já baixados |
| `radar` | — | descobre lançamentos novos via feed RSS |
| `stats` | — | estatísticas da sua biblioteca |

Rode `qobuz-dl <comando> —help` para ver todas as opções de cada um.

—

### 5.1 `dl` — download direto

O comando principal. Aceita URLs de **álbum, faixa, artista, gravadora ou playlist**, várias separadas por espaço, ou um arquivo de texto com uma URL por linha.

```bash
qobuz-dl dl https://open.qobuz.com/album/xxxxx
qobuz-dl dl url1 url2 url3
qobuz-dl dl minha-lista.txt
```

Passando a URL de um **artista**, ele baixa a discografia inteira. Passando a de uma **gravadora**, o catálogo dela.

#### Filtros de seleção

| Flag | Efeito |
|—|—|
| `—dry-run` | mostra o que seria baixado, sem baixar — **use sempre antes de uma discografia grande** |
| `—since DATE` | apenas lançamentos a partir da data (`YYYY-MM-DD` ou `YYYY`) |
| `—before DATE` | apenas lançamentos anteriores à data |
| `—albums-only` | ignora singles, EPs e coletâneas de vários artistas |
| `-s`, `—smart-discography` | tenta filtrar tributos, karaokê e spam ao baixar discografias |
| `-b`, `—blacklist ARQUIVO` | arquivo com palavras-chave a ignorar (veja `blacklist_example.txt`) |
| `—booklet-only` | baixa **apenas** o encarte digital e PDFs, pulando o áudio |

#### Modos de reprocessamento

| Flag | Efeito |
|—|—|
| `—tag-only` | reaplica as tags nos arquivos existentes **sem baixar de novo**; pula os ausentes |
| `—verify-download` | após cada faixa, decodifica com `ffmpeg` para detectar corrupção **real no áudio** (não só falha de tag) |
| `—musicbrainz` | busca e embute MBIDs via ISRC; custa cerca de 1 s por faixa |

`—tag-only` é o que você usa depois de mudar suas preferências de tag: corrige a biblioteca inteira sem gastar banda.

#### Desempenho

| Flag | Padrão | Observação |
|—|—|—|
| `—max-workers N` | `1` | downloads paralelos. Valores altos aumentam o risco de bloqueio pela API |
| `—segment-workers N` | — | threads do fallback de download segmentado |
| `—delay SEGUNDOS` | — | pausa entre faixas; imita comportamento humano |

O **download segmentado** é um fallback automático: quando a Akamai (CDN do Qobuz) bloqueia o download direto, o programa passa a buscar o arquivo em partes paralelas. Vale saber que esse caminho estava quebrado por um bug de desempacotamento até a correção mais recente — se você já viu downloads falharem misteriosamente em faixas grandes, era isso.

#### Organização

| Flag | Efeito |
|—|—|
| `-d`, `—directory PATH` | pasta de destino |
| `-ff`, `—folder-format` | ver [seção 8](#8-formatação-de-pastas-e-nomes) |
| `-tf`, `—track-format` | idem |
| `—no-m3u` | não gera `.m3u` ao baixar playlists |
| `—playlist-as-albums` | organiza itens de playlist na estrutura de pastas do álbum original |
| `—no-db` | ignora o banco: baixa mesmo o que já existe |
| `—multiple-disc-prefix CD` | prefixo das pastas de disco |
| `—multiple-disc-one-dir` | joga todos os discos numa pasta só |
| `—multiple-disc-track-format` | formato de faixa específico para múltiplos discos |

—

### 5.2 `interactive` / `i` / `fun` — busca interativa

```bash
qobuz-dl i
qobuz-dl i -l 50        # limite de resultados
```

Abre uma interface de seleção construída com `questionary`: você pesquisa, navega com as setas, marca vários itens com espaço e confirma. Aceita as mesmas opções de download do `dl`.

O alias `fun` existe por retrocompatibilidade com o projeto original.

—

### 5.3 `lucky` — “estou com sorte”

Baixa direto os primeiros resultados de uma busca, sem menu:

```bash
qobuz-dl lucky “Cowboy Bebop OST”
qobuz-dl lucky “Yoko Kanno” -t artist -n 3
```

| Flag | Padrão | Valores |
|—|—|—|
| `-t`, `—type` | `album` | `artist`, `album`, `track`, `playlist` |
| `-n`, `—number` | `1` | quantos resultados baixar |

Prático para caçar trilhas sonoras em lote a partir de uma lista de nomes.

—

### 5.4 `import-playlist` / `ip` — trazer playlists de fora

Migra playlists de **Spotify, Deezer e Apple Music** para o Qobuz, por URL ou por arquivo exportado:

```bash
qobuz-dl ip https://open.spotify.com/playlist/xxxxx
qobuz-dl ip minha-playlist.csv —name “Favoritas 2026”
qobuz-dl ip export.json —auto
```

**Formatos de arquivo aceitos:**

| Formato | Detalhes |
|—|—|
| `.txt` | uma entrada por linha, `Artista - Título` ou `Artista: Título`; linhas com `#` são comentários |
| `.csv` | qualquer export com colunas `artist` e `title` (Exportify, Soundiiz, TuneMyMusic); separador `,` `;` ou tab detectado automaticamente |
| `.json` | exports do Spotify e do exportify.net; entende `[{artist,title}]`, `[{trackName,artistName}]` e `[{track:{name,artists:[{name}]}}]` |

Cada faixa passa por **correspondência aproximada (fuzzy matching)** contra o catálogo do Qobuz — o mesmo pipeline usado na integração com o Last.fm. Quando a confiança é baixa, ele pergunta.

- `—auto` aceita automaticamente qualquer correspondência com **60% ou mais** de similaridade. Acelera muito, mas erra em faixas de nome genérico ou com muitas regravações.
- `—name`, `-n` define a pasta de destino (o padrão é o nome do arquivo).

—

### 5.5 `sync-playlist` / `sp` — espelhar uma playlist

Mantém uma pasta local **idêntica** a uma playlist do Qobuz:

```bash
qobuz-dl sp https://play.qobuz.com/playlist/12345
qobuz-dl sp <url> —yes        # sem confirmação
```

Ele compara o conteúdo local com o remoto, **baixa o que falta e apaga o que foi removido** da playlist, além de limpar pastas que ficaram vazias.

Atenção: isso **deleta arquivos locais**. Rode sem `—yes` na primeira vez para ver o que ele pretende remover. O `—yes` é para uso em cron, depois que você já confia no comportamento.

—

### 5.6 `lyrics` — letras retroativas

Escaneia uma pasta e injeta letras nos arquivos que já estão em disco:

```bash
qobuz-dl lyrics
qobuz-dl lyrics “/caminho/da/musica”
```

Ele recupera o ID da faixa do Qobuz que está gravado nas tags, busca as letras e injeta. Também **inspeciona o que já existe** antes, para não sobrescrever letras boas por versões piores. Detalhes na [seção 10](#10-letras-e-tradução).

—

### 5.7 `radar` — lançamentos novos

```bash
qobuz-dl radar
```

Funciona a partir de um **feed RSS** de lançamentos (na primeira execução ele pede a URL e salva no `config.ini`). O fluxo é:

1. Lê e interpreta o feed.
2. Pesquisa cada lançamento no Qobuz — **sequencialmente de propósito**, para não disparar muitas buscas simultâneas contra a API.
3. Monta um menu com o que encontrou, para você escolher o que baixar.

—

### 5.8 `stats` — estatísticas da biblioteca

```bash
qobuz-dl stats
qobuz-dl stats —artistas     # lista completa de artistas
```

Leitura puramente local (não precisa de rede nem de login). Mostra total de downloads, álbuns, faixas avulsas, artistas e álbuns únicos, distribuição por qualidade e formato, com medidores visuais, e um ranking dos artistas mais baixados.

—

## 6. Operações globais de manutenção

Estas não são subcomandos — são flags do comando principal.

### `—sync-db [PATH]` — reconstruir o banco a partir do disco

```bash
qobuz-dl —sync-db
qobuz-dl —sync-db “/media/hd/musica”
```

Escaneia os arquivos locais, lê os IDs do Qobuz gravados nas tags e **restaura as entradas ausentes** no banco. É o que salva a sua vida quando você perde o `qobuz_dl.db`, troca de máquina ou migra a biblioteca: sem isso, o programa acharia que nada foi baixado e reprocessaria tudo.

### `—find-duplicates [PATH]` — duplicatas por som, não por nome

```bash
qobuz-dl —find-duplicates “/media/hd/musica”
```

Sistema **híbrido**, escolhido automaticamente:

- **No desktop**, usa impressão digital acústica (Chromaprint/AcoustID via `fpcalc`). Isso identifica a mesma gravação mesmo com nomes de arquivo e tags completamente diferentes — inclusive entre um FLAC e um MP3.
- **Em celular/iPad**, cai para comparação por metadados (artista + título + duração), já que `fpcalc` não existe nesses ambientes.

### `—watch [PATH]` — vigiar uma pasta

```bash
qobuz-dl —watch “/media/hd/musica”
```

Usa `watchdog` para monitorar a pasta e rodar a retro-marcação de letras automaticamente sempre que novos arquivos de áudio aparecerem. Fica em execução até você interromper.

### `-p`, `—purge` — apagar o banco

Zera o histórico de downloads. Os arquivos de áudio não são tocados, mas o programa passa a não saber mais o que já tem — considere `—sync-db` em vez disso.

### `-sc`, `—show-config` e `-r`, `—reset`

Mostram a configuração atual e recriam o `config.ini`, respectivamente.

—

## 7. Qualidade de áudio

| Valor | Formato | Descrição |
|—|—|—|
| `5` | MP3 320 kbps | com perda |
| `6` | FLAC 16 bit / 44,1 kHz | lossless, qualidade de CD |
| `7` | FLAC 24 bit até 96 kHz | Hi-Res |
| `27` | FLAC 24 bit acima de 96 kHz | Hi-Res máximo |

```bash
qobuz-dl dl <url> -q 27
```

Por padrão existe **fallback automático**: se a qualidade pedida não estiver disponível, ele baixa a melhor disponível abaixo dela. Com `—no-fallback`, o item é **pulado** em vez de rebaixado — o que você quer se está montando uma coleção estritamente Hi-Res.

Nem todo álbum tem 24 bits. Pedir `-q 27` numa discografia antiga significa, na prática, receber quase tudo em `6` (com fallback) ou quase nada (sem fallback).

—

## 8. Formatação de pastas e nomes

### Formato de pasta (`-ff`, `—folder-format`)

Chaves disponíveis:

```
album_id          album_url        album_title      album_title_base
album_artist      album_genre      album_composer   label
copyright         upc              barcode          release_date
year              media_type       format           bit_depth
sampling_rate     album_version    disc_count       track_count
```

Você pode usar `/` para criar **subdiretórios**:

```bash
-ff “{album_artist}/{year} - {album_title} [{bit_depth}B-{sampling_rate}kHz]”
```

O `-fbff`, `—fallback-folder-format` define um padrão alternativo usado quando o principal falha (tipicamente por uma chave ausente nos metadados do álbum).

### Formato de faixa (`-tf`, `—track-format`)

```
album_title       album_title_base   album_artist     track_id
track_artist      track_composer     track_number     isrc
bit_depth         sampling_rate      track_title      track_title_base
version           year               disc_number      release_date
```

```bash
-tf “{track_number} - {track_artist} - {track_title}”
```

O nome não pode conter caracteres proibidos pelo sistema de arquivos. O programa já limpa isso automaticamente; a opção `legacy_charmap` no `config.ini` controla se caracteres inválidos são removidos ou convertidos para equivalentes de largura total (`：`, `？`), que preservam a aparência do título.

A diferença entre `album_title` e `album_title_base` é a versão: `album_title` inclui coisas como “(Remastered 2011)”, `album_title_base` não.

—

## 9. Metadados e tags

O ponto forte do projeto. Cada campo tem uma flag `—no-*-tag` para desligar individualmente:

**Identificação:** `album-artist`, `album-title`, `track-artist`, `track-title`, `track-number`, `track-total`, `disc-number`, `disc-total`

**Editorial:** `release-date`, `media-type`, `genre`, `composer`, `copyright`, `label`, `explicit`

**Identificadores:** `upc` (código de barras do álbum), `isrc` (código da gravação), `album-url` (a URL do Qobuz, gravada na própria tag)

**Áudio:** `replaygain` — ganho de normalização, para que os volumes não pulem entre álbuns.

### Multi-valor

Campos como gênero vêm do Qobuz separados por vírgula. Com `—multi-tags`, viram **múltiplos campos de tag** em vez de uma string única — o que players e bibliotecas modernas tratam corretamente. `—no-multi-tags` desliga, sobrepondo o `config.ini`.

### MusicBrainz

`—musicbrainz` consulta o MusicBrainz pelo ISRC de cada faixa e embute os MBIDs. Isso conecta a sua biblioteca ao ecossistema de ferramentas que usam MusicBrainz como identificador canônico (Picard, Beets, Navidrome). Custo: cerca de 1 segundo por faixa e dependência de internet.

### Créditos

O programa gera um arquivo **`Digital Booklet.txt`** com créditos completos e notas do álbum. Controle com `—no-credits` e `—with-credits` (esta última sobrepõe o `config.ini`).

### Idioma dos metadados

Por padrão, o idioma é **forçado para inglês** para manter consistência. `—native-lang` desativa isso e traz os metadados no idioma nativo da conta.

—

## 10. Letras e tradução

Um dos subsistemas mais elaborados. As fontes são consultadas em cascata:

1. **Letras nativas do Qobuz** — incluem marcação de tempo linha a linha, permitindo gerar `.lrc` sincronizado de verdade.
2. **Tradução do Qobuz** — quando o álbum tem tradução disponível.
3. **LRCLIB** — gratuito, sem necessidade de API key.
4. **Genius** — fallback, requer token da API e o pacote `lyricsgenius`.

### O `.lrc` bilíngue

Quando existem original e tradução, o programa monta um `.lrc` **bilíngue**: ele interpreta os dois arquivos, casa as linhas pelas marcações de tempo e intercala. O resultado é a letra original e a tradução aparecendo juntas, sincronizadas, no seu player.

O idioma da tradução é definido por `lyrics_translation_lang` no `config.ini` (o padrão gerado é `pt`).

### Onde as letras vão

| Flag | Efeito |
|—|—|
| *(padrão)* | embute nas tags **e** salva `.lrc` externo |
| `—no-lrc-files` | não salva os arquivos `.lrc` |
| `—no-embed-lyrics` | não embute nas tags; salva apenas `.lrc`/`.txt` |
| `—no-lyrics` | desliga letras completamente nesta sessão |

### Retro-marcação

O comando `lyrics` e a flag `—watch` aplicam letras em arquivos que já estão em disco. O módulo responsável **inspeciona as letras existentes** antes de agir, comparando o que já está lá com o que buscou, para não trocar uma letra sincronizada boa por uma versão simples. Ele recupera o ID da faixa direto das tags gravadas no arquivo.

—

## 11. Capas e encartes

| Flag | Efeito |
|—|—|
| `-e`, `—embed-art` | embute a capa no arquivo de áudio |
| `—og-cover` | baixa a capa na resolução original |
| `—no-cover` | não baixa capa nenhuma |
| `—embedded-art-size` | `50`, `100`, `150`, `300`, `600`, `max`, `org` |
| `—saved-art-size` | idem, para o arquivo de capa salvo na pasta |
| `—booklet-only` | baixa somente o encarte digital e PDFs extras |

Existe um detalhe técnico relevante: o FLAC tem **limite de 16 MB** para imagem embutida. Quando a capa original excede isso, o programa usa o Pillow para recompactá-la automaticamente até caber — por isso o Pillow é dependência.

Álbuns do Qobuz frequentemente trazem **Digital Booklet em PDF**, que o programa baixa junto.

—

## 12. Controle da saída no terminal

Esta área foi reescrita na atualização mais recente. Antes, a saída vinha de três canais sem coordenação (`print`, `logging` e `tqdm.write`), a largura era calculada em sete lugares com limites diferentes e não havia nenhum controle de verbosidade.

Hoje existe uma **camada única de apresentação** (`qobuz_dl/ui.py`) por onde tudo passa.

### As três flags

```bash
qobuz-dl —quiet dl <url>        # só avisos e erros — ideal para cron
qobuz-dl —verbose dl <url>      # diagnóstico nível DEBUG
qobuz-dl —no-color dl <url>     # sem cores ANSI
```

Elas funcionam **antes ou depois** do subcomando — `qobuz-dl stats —quiet` e `qobuz-dl —quiet stats` são equivalentes. (Na versão anterior, a primeira forma falhava com “unrecognized arguments”.)

### Adaptação automática

- **Largura**: a interface se ajusta ao terminal, entre 32 e 100 colunas, com dois pontos de quebra de layout (60 e 90 colunas). Testado de 32 a 120 colunas sem estouro de linha.
- **Cor**: detectada respeitando, nesta ordem, `NO_COLOR`, `FORCE_COLOR`, `TERM=dumb` e se a saída é um terminal de verdade. Em pipe, sai limpo automaticamente.
- **Glifos**: em terminais sem UTF-8, `━` vira `=`, `█` vira `#` e `─` vira `-`. Se ainda assim houver erro de codificação, há um fallback — antes, um terminal legado do Windows podia derrubar o download inteiro por causa de um caractere.

### Vocabulário visual

```
[+] operação concluída
[*] etapa em andamento
[!] aviso
[-] item pulado
```

Mensagens de log de todos os módulos passam pelo mesmo canal que as barras de progresso, de modo que texto e barras não competem pela mesma linha.

—

## 13. Variáveis de ambiente

| Variável | Efeito |
|—|—|
| `CONFIG_DIR` | redireciona a pasta de configuração — útil para perfis múltiplos |
| `QOBUZ_DL_IOS_HOME` | força o diretório base no iOS/a-Shell |
| `NO_COLOR` | desliga cores ([padrão no-color.org](https://no-color.org/)) |
| `FORCE_COLOR` | força cores mesmo sem terminal (útil com `\| less -R`) |
| `COLUMNS` | força a largura da interface |
| `TERM=dumb` | desliga cores |

`NO_COLOR` tem precedência sobre `FORCE_COLOR`.

—

## 14. O config.ini completo

Arquivo INI com uma seção `[qobuz]`. Toda opção pode ser sobreposta na linha de comando.

### Conta e acesso

| Chave | Descrição |
|—|—|
| `email` | e-mail da conta |
| `password` | vazio — não é mais usado pela API |
| `auth_token` | token do navegador (vazio se estiver no keyring) |
| `user_auth_token` | token alternativo |
| `disable_keyring` | `true` para guardar tokens no arquivo em vez do keyring |
| `app_id`, `secrets` | credenciais da API, resolvidas automaticamente |

### Padrões de download

| Chave | Padrão gerado |
|—|—|
| `directory` | `Qobuz Downloads` |
| `default_quality` | `27` |
| `default_limit` | `500` |
| `folder_format` | definido no assistente |
| `fallback_folder_format` | `{album_artist} - {album_title}` |
| `track_format` | `{track_number} - {track_title}` |
| `max_workers` | `1` |
| `smart_discography` | `false` |
| `albums_only` | `false` |
| `no_fallback` | `false` |
| `no_database` | `false` |
| `no_m3u` | `false` |
| `blacklist` | `blacklist.txt` |
| `legacy_charmap` | `false` |

### Letras

| Chave | Padrão |
|—|—|
| `fetch_lyrics` | definido no assistente |
| `embed_lyrics` | `true` |
| `no_lrc_files` | `true` |
| `lyrics_translation_lang` | `pt` |
| `genius_token` | vazio ou no keyring |

### Capas

| Chave | Padrão |
|—|—|
| `embed_art` | `true` |
| `og_cover` | `true` |
| `no_cover` | `false` |
| `embedded_art_size` | `org` |
| `saved_art_size` | `org` |

### Múltiplos discos

| Chave | Padrão |
|—|—|
| `multiple_disc_prefix` | `CD` |
| `multiple_disc_one_dir` | `false` |
| `multiple_disc_track_format` | definido no código |

### Tags

Todas as chaves `no_*_tag` (`no_album_artist_tag`, `no_genre_tag`, `no_replaygain_tag`, `no_isrc_tag`, `no_upc_tag`, e assim por diante) vêm como `false`. Há também `multi_value_tags` (`false`) e `no_credits` (`false`).

### Interface

| Chave | Descrição |
|—|—|
| `accent_color` | RGB da cor de destaque, ex. `95,168,211` |

—

## 15. O banco de dados

SQLite em `qobuz_dl.db`, tabela `downloads`:

| Coluna | Descrição |
|—|—|
| `id` | ID do Qobuz |
| `media_type` | `album` ou `track` |
| `quality` | qualidade pedida |
| `file_format` | FLAC, MP3 |
| `quality_met` | se a qualidade pedida foi de fato obtida |
| `bit_depth`, `sampling_rate` | características reais do arquivo |
| `saved_path` | onde foi salvo |
| `status` | estado do download |
| `url` | URL de origem |
| `release_date`, `artist`, `album` | metadados para as estatísticas |

A **chave primária é `(id, quality)`**, não apenas `id`. Isso é intencional e importante: você pode ter o mesmo álbum registrado em qualidades diferentes, e um upgrade de 6 para 27 não sobrescreve o registro anterior.

O `create_db()` faz **migração automática de esquema**, inclusive de bancos antigos que não tinham as colunas de qualidade, artista e álbum. Bancos da v1 são migrados preservando o histórico de IDs.

—

## 16. Automação e integração

### `—progress-json` — para interfaces gráficas

```bash
qobuz-dl dl <url> —progress-json
```

Emite **uma linha JSON por evento** no stdout (`track-start`, `track-done`), pensado para ser consumido por uma GUI web ou app que envolva o downloader. Combine com `—quiet` para ter um stream limpo de máquina.

Uma limitação a conhecer: **não existe evento `track_failed`**. Uma interface que consuma isso não é notificada de falhas — precisa inferir por timeout ou pelo código de saída.

### Cron

```cron
# radar diário às 8h
0 8 * * * qobuz-dl —quiet radar

# sincronizar uma playlist toda madrugada
30 3 * * * qobuz-dl —quiet sp https://play.qobuz.com/playlist/12345 —yes
```

O `—quiet` evita e-mails do cron a cada execução, e `—no-color` mantém os logs legíveis (embora as cores já sejam desligadas automaticamente quando a saída não é um terminal).

—

## 17. Arquitetura interna

25 módulos, cerca de 12.600 linhas.

### Ponto de entrada e interface

| Módulo | Linhas | Papel |
|—|—|—|
| `cli.py` | 1227 | ponto de entrada, roteamento, assistente de configuração |
| `commands.py` | 764 | definição de todo o argparse |
| `ui.py` | 556 | camada única de apresentação no terminal |
| `color.py` | 165 | paleta e cor de destaque |
| `stats_view.py` | 133 | renderização do comando `stats` |

### Núcleo de download

| Módulo | Linhas | Papel |
|—|—|—|
| `downloader.py` | 2520 | download, barras de progresso, fallback segmentado |
| `core.py` | 1873 | classe `QobuzDL`, orquestração, seleção interativa |
| `qopy.py` | 969 | cliente da API do Qobuz |
| `metadata.py` | 736 | escrita de tags FLAC/MP3, capas embutidas |
| `bundle.py` | 89 | extração de `app_id` e secrets |

### Funcionalidades

| Módulo | Linhas | Papel |
|—|—|—|
| `utils.py` | 633 | m3u, filtro de discografia, limpeza de nomes, caminhos |
| `retro_tagger.py` | 609 | injeção retroativa de letras |
| `lyrics_engine.py` | 471 | cascata de fontes de letras, `.lrc` bilíngue |
| `sync.py` | 354 | `—sync-db` e `—find-duplicates` |
| `sync_playlist.py` | 306 | espelhamento de playlist |
| `playlist_import.py` | 273 | parser de exports de outras plataformas |
| `radar.py` | 206 | feed RSS de lançamentos |
| `musicbrainz.py` | 140 | MBID via ISRC |
| `watcher.py` | 141 | monitoramento de pasta |
| `db.py` | 348 | SQLite e migrações |
| `settings.py` | 329 | fusão de CLI + config.ini |

### Como as opções são resolvidas

Este é o ponto mais delicado da arquitetura. `settings.py` funde três fontes: **padrões do código → `config.ini` → argumentos da CLI**, com a CLI vencendo.

A implementação usa uma sentinela `_MISSING` com os helpers `_merge_bool_opt_out()` e `_merge_bool_opt_in()`. Isso existe porque o padrão ingênuo `getattr(args, x, False) or config[...]` torna **impossível desligar pela CLI** algo que o `config.ini` liga — o `or` sempre deixa o valor do config vencer. Vários bugs históricos de “a flag não faz nada” vinham dessa raiz.

### Concorrência

O projeto é `asyncio` com `httpx`. Os downloads paralelos usam workers coordenados por locks por diretório, e as barras de progresso são gerenciadas por um pool de posições para não se sobrescreverem. O `tenacity` cuida das retentativas de rede.

—

## 18. Solução de problemas

### Erro de autenticação

O token expirou (acontece periodicamente) ou o keyring falhou. Refaça com `qobuz-dl -r`. Em Linux headless, NAS ou Docker, desative o keyring.

### “Nada foi baixado” numa discografia

Provavelmente `—no-fallback` está ativo e nenhum álbum atende à qualidade pedida. Rode com `—dry-run` para ver a decisão sem gastar banda.

### O programa acha que já baixou tudo

O banco tem os registros mas você não tem os arquivos (ou o contrário). Use `—sync-db` para reconciliar a partir do disco. `—no-db` força o download ignorando o histórico.

### Downloads falham em faixas grandes

Se você usa uma versão anterior à correção do download segmentado, o fallback acionado quando a CDN bloqueia o download direto estava quebrado por um erro de desempacotamento — ele falhava na primeira linha. Atualize. Verifique também `—max-workers`: valores altos aumentam o risco de bloqueio.

### `—find-duplicates` não funciona

Falta o `fpcalc` do Chromaprint. Instale `libchromaprint-tools` (Debian/Ubuntu) ou `chromaprint` (Homebrew). Em mobile, o modo por metadados é usado automaticamente.

### `—verify-download` falha sempre

Falta o `ffmpeg` no PATH. Nenhuma das duas dependências externas é checada na inicialização, então o erro só aparece na hora do uso.

### Sem cores ao usar pipe

Isso é intencional. Para forçar, use `FORCE_COLOR=1 qobuz-dl ... | less -R`.

### Interface quebrada em terminal antigo do Windows

A degradação de glifos deve resolver. Se persistir, `—no-color` e um terminal com UTF-8 (Windows Terminal) são o caminho.

### `SyntaxError` ao instalar

Você está em Python 3.8 ou anterior. A versão mínima real é **3.9** — versões antigas do pacote declaravam `>=3.6` incorretamente, permitindo instalar onde o código não roda.

—

## 19. Receitas prontas

### Arquivo Hi-Res estrito, organizado por artista e ano

```bash
qobuz-dl dl <url-do-artista> \
  -q 27 —no-fallback \
  —albums-only —smart-discography \
  -ff “{album_artist}/{year} - {album_title} [{bit_depth}B-{sampling_rate}kHz]” \
  -tf “{track_number} - {track_title}” \
  —musicbrainz —verify-download -e
```

### Ver antes de comprometer

```bash
qobuz-dl dl <url-do-artista> —dry-run —since 2020
```

### Reprocessar tags da biblioteca sem baixar de novo

```bash
qobuz-dl dl minha-lista.txt —tag-only —multi-tags —musicbrainz
```

### Trilhas sonoras de anime em lote

```bash
while read -r nome; do
  qobuz-dl —quiet lucky “$nome OST” -n 2 -q 27
done < lista-de-animes.txt
```

### Perfis separados por biblioteca

```bash
# arquivo Hi-Res
CONFIG_DIR=~/perfis/hires qobuz-dl -r

# biblioteca de MP3 para o celular
CONFIG_DIR=~/perfis/mobile qobuz-dl -r

# usando
CONFIG_DIR=~/perfis/hires qobuz-dl dl <url>
```

Cada perfil tem `config.ini` e banco próprios.

### Manutenção mensal

```bash
qobuz-dl —sync-db “/media/hd/musica”
qobuz-dl —find-duplicates “/media/hd/musica”
qobuz-dl lyrics “/media/hd/musica”
qobuz-dl stats
```

### Encartes de uma discografia, sem o áudio

```bash
qobuz-dl dl <url-do-artista> —booklet-only
```

—

## Referências

- [Repositório do projeto](https://github.com/kaduvercosa/qobuz-dl-ultra)
- [a-Shell para iOS](https://holzschu.github.io/a-Shell_iOS/) — ambiente suportado em iPhone/iPad
- [LRCLIB](https://lrclib.net/) — fonte gratuita de letras sincronizadas
- [Chromaprint / AcoustID](https://acoustid.org/chromaprint) — o `fpcalc` usado em `—find-duplicates`
- [MusicBrainz](https://musicbrainz.org/) — identificadores embutidos com `—musicbrainz`
- [Padrão NO_COLOR](https://no-color.org/) — respeitado pela camada de interface
- [ReplayGain](https://en.wikipedia.org/wiki/ReplayGain) — normalização de volume
