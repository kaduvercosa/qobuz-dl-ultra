# 𝗤𝗢𝗕𝗨𝗭-𝗗𝗟 𝗨𝗟𝗧𝗥𝗔
[![Versão PyPI](https://img.shields.io/pypi/v/qobuz-dl-ultra.svg)](https://pypi.org/project/qobuz-dl-ultra/) [![Downloads PyPI](https://static.pepy.tech/personalized-badge/qobuz-dl-ultra?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/qobuz-dl-ultra) ![Docker Image CI](https://github.com/kaduvercosa/qobuz-dl-ultra/actions/workflows/docker.yml/badge.svg) [![codecov](https://codecov.io/gh/kaduvercosa/qobuz-dl-ultra/branch/master/graph/badge.svg)](https://codecov.io/gh/kaduvercosa/qobuz-dl-ultra)

## 📑 Índice

* [✨ Funcionalidades](#-funcionalidades)
  * [🎧 Mecanismo Audiófilo e de Metadados](#-mecanismo-audiófilo-e-de-metadados)
  * [🚀 Mecanismo de Download Resiliente](#-mecanismo-de-download-resiliente)
  * [📁 Formatação Avançada e Armazenamento](#-formatação-avançada-e-armazenamento)
  * [❤️ Sincronização Nativa de Favoritos e Menu Interativo](#️-sincronização-nativa-de-favoritos-e-menu-interativo)
  * [🌉 Integração Inteligente com Last.fm e Modo Interativo](#-integração-inteligente-com-lastfm-e-modo-interativo)
  * [🛡️ Gerenciamento de Pastas À Prova de Falhas e Retomada Inteligente](#️-gerenciamento-de-pastas-à-prova-de-falhas-e-retomada-inteligente)

* [📥 Instalação e Configuração](#-instalação-e-configuração)
  * [Opção A: 📦 Pacote PyPI (Recomendado)](#opcao-a-pypi)
  * [Opção B: Código-fonte Python (Avançado)](#opcao-b-codigo-fonte)
  * [Opção C: 🐳 Uso com Docker](#opcao-c-docker)
  * [⚙️ Configuração e Caminhos Personalizados](#️-configuração-e-caminhos-personalizados)
  * [🔑 Como obter seu Auth Token](#-como-obter-seu-auth-token)

* [💻 Uso e Exemplos Rápidos](#-uso-e-exemplos-rápidos)
  * [🗄️ Gerenciamento de Banco de Dados e Biblioteca](#️-gerenciamento-de-banco-de-dados-e-biblioteca)
  * [🛠️ Principais Variáveis de Formatação](#️-principais-variáveis-de-formatação)

* [🔧 Solução de Problemas: Ambientes Headless e Servidores](#-solução-de-problemas-ambientes-headless-e-servidores)
* [🏆 Créditos](#-créditos)
* [⚠️ Isenção de Responsabilidade (Aviso Legal)](#️-isenção-de-responsabilidade-aviso-legal)

__Pesquise, explore e baixe músicas Lossless e Hi-Res do [Qobuz](https://www.qobuz.com/).__

### Uso em celular e iPad

A interface interativa adapta tabelas e cartões à largura do terminal. Para
manter a experiência consistente entre um celular e um iPad, é possível
forçar o modo visual desejado:

```bash
qobuz-dl interactive --ui compact
qobuz-dl interactive --ui comfortable
```

`--ui auto` é o padrão e continua detectando a largura automaticamente.
Em dispositivos com pouco espaço livre, a verificação é automática: o programa
estima o tamanho do álbum antes de começar e interrompe a operação com uma
mensagem clara quando o download não cabe. Para desligá-la em um ambiente com
quota ou montagem de disco incomum, use `--no-check-storage`:

```bash
qobuz-dl dl "https://play.qobuz.com/album/..." --no-check-storage
```

No modo interativo, itens repetidos são removidos automaticamente da fila.
O fluxo é rotativo: depois de adicionar resultados, `✅ Sim` inicia uma nova
pesquisa, `🏠 Menu inicial` permite escolher outra categoria sem perder a fila
e `❌ Não` leva à revisão final. Os submenus de favoritos e artistas também
oferecem retorno ao menu inicial; durante a pesquisa, `/menu` (ou `:menu`) faz
o mesmo e `/novo` começa outra busca na categoria atual. Dentro de qualquer
tabela ou cartão, `n` inicia nova pesquisa e `m` volta imediatamente ao menu
inicial, sem precisar navegar até o fim da lista. Ao explorar um artista, `b`
volta aos resultados de artistas; assim, o retorno do artista não é confundido
com o menu inicial.

Antes de iniciar o download, o programa mostra um resumo por tipo e permite
revisar a fila com nomes de artista/título, remover itens selecionados ou
cancelar a operação. Em qualquer lista de resultados, use `a` para ordenar
alfabeticamente (A-Z), `y` para ordenar por ano do mais novo ao mais antigo e
`Y` para inverter a ordem do ano. Nas listas de faixas, o ano aparece no lugar
da duração em todos os layouts. Discografias com mais de 20 álbuns também
exigem confirmação explícita antes de entrar na fila.

Metadados de leitura da API podem ser reutilizados automaticamente por até 12
horas, reduzindo chamadas repetidas em playlists, discografias e sincronizações.
Tokens, credenciais e URLs assinadas nunca entram nesse cache. Para desativá-lo,
defina `QOBUZ_DL_DISABLE_METADATA_CACHE=1`; o caminho, TTL e limite também podem
ser ajustados com `QOBUZ_DL_METADATA_CACHE`, `QOBUZ_DL_METADATA_CACHE_TTL` e
`QOBUZ_DL_METADATA_CACHE_MAX`.

Durante o download, `.report.json` continua sendo atualizado a cada faixa, mas
o `report.html` só é regenerado ao finalizar o álbum. Isso reduz escrita e
processamento no iPad sem perder a retomada ou o diagnóstico incremental.

## ✨ Funcionalidades

### 🎧 Mecanismo Audiófilo e de Metadados
* **Otimizado para Roon e DAP:**  
  Metadados, capas e letras são meticulosamente formatados para garantir integração perfeita e imediata com servidores Roon e Tocadores de Áudio Digital (DAPs).
 
* **Tagging Direto de URL do Álbum:**  
  Gera e incorpora automaticamente um link clicável direto `QOBUZ ALBUM URL` nos metadados da faixa (Vorbis Comments para FLAC, quadro `TXXX` para MP3).  
  Isso permite o acesso com um clique à página original do álbum no Qobuz diretamente de editores de tags como o Mp3tag ou reprodutores audiófilos compatíveis. Você pode opcionalmente desativar essa tag não padrão usando a flag `--no-album-url-tag` (ou `no_album_url_tag = true` no arquivo de configuração).
 
* **Letras Sincronizadas Prontas para o Roon:**  
  O mecanismo formata e incorpora inteligentemente dados `.lrc` com marcação de tempo diretamente nos arquivos de áudio (Vorbis Comments `[LYRICS]`), garantindo que o Roon exiba nativamente letras roláveis em estilo karaokê na visualização “Tocando Agora” (Now Playing).  
  Se você preferir uma estrutura de pastas minimalista e organizada, pode desativar totalmente a geração de arquivos `.lrc` externos via CLI (`--no-lrc-files`). Por outro lado, se preferir arquivos externos sem inflar os metadados do áudio, use a nova flag `--no-embed-lyrics` (ou defina `embed_lyrics = false` na configuração).  
  A fonte **BiniLyrics** (letras estilo Apple Music, escolhidas pelo ISRC da faixa) já entra na busca automaticamente; use `--lyrics-word-sync` (ou `lyrics_word_sync = true`) no config.ini para gravar a sincronização por palavra quando ela existir — a maioria dos players só entende por linha.
 
* **Controle Amplo de Tags:**  
  O mecanismo de tags reformulado suporta metadados altamente detalhados de música clássica. Quase todas as tags podem ser ativadas/desativadas via argumentos de linha de comando (CLI).
 
* **Tradução Inteligente de Gêneros:**  
  Traduz automaticamente gêneros persistentes em francês (ex.: *Électronique*, *Bande Originale*) para o inglês padrão, garantindo que sua biblioteca permaneça consistente e pesquisável.
 
* **Tagging Nativo Multi-Artista / Multi-Valor e Análise Profunda de Intérpretes:**  
  Detecta e separa automaticamente artistas principais, participações especiais e extrai *todos* os compositores/letristas de strings de metadados complexas do Qobuz.  
  Com a nova flag CLI `--multi-tags`, o mecanismo divide inteligentemente metadados separados por vírgula em tags multi-valor discretas para FLAC (Vorbis Comments) e MP3 (ID3v2.4), garantindo interpretação impecável da biblioteca por players avançados como Roon, MusicBee ou Plexamp.
 
* **Suporte Nativo a ReplayGain:**  
  Extrai e incorpora automaticamente as tags `REPLAYGAIN_TRACK_GAIN` e `REPLAYGAIN_TRACK_PEAK` diretamente dos dados ocultos da API do Qobuz. Isso garante nivelamento de volume perfeito e não destrutivo de fábrica para tocadores de áudio digital (DAPs) de alta fidelidade e servidores audiófilos como o Roon.
 
* **Mecanismo Automático de Letras e Tagger Retroativo:**  
  Busca e injeta letras sincronizadas (`.lrc`) e não sincronizadas usando o LRCLIB (com fallback para a API do Genius). Inclui o comando dedicado `lyrics` para escanear retroativamente e injetar letras ausentes em sua biblioteca local existente sem precisar baixar novamente o áudio.
 
* **Encartes Digitais Aprimorados (Digital Booklets):**  
  Compila automaticamente um arquivo `.txt` formatado com lista de faixas completa, duração, créditos detalhados, metadados e resenhas. Ao concluir, o mecanismo varre a pasta, remove as marcações de tempo dos arquivos `.lrc` e anexa as letras em texto puro de todo o álbum diretamente no encarte. “Goodies” oficiais em PDF também são baixados junto. **Agora você pode usar a flag `--booklet-only` para baixar exclusivamente esses arquivos de metadados, capas e PDFs, ignorando graciosamente todas as faixas de áudio pesadas.**
 
* **Correção do Campo de Compositor:**  
  Extrai meticulosamente cada compositor individual da string completa de intérpretes, acabando com o problema de metadados de compositores truncados ou “aleatórios”.
 
* **Formatação Inteligente de Datas:**  
  Padroniza as datas de lançamento em entradas únicas e limpas, evitando conflitos de tags duplicadas de ano/data em softwares de reprodução.
 
* **Modo Bit-Perfect & Purista:**  
  Desative completamente as tags de volume ReplayGain e Peak usando a flag CLI `--no-replaygain-tag` (ou `no_replaygain_tag = true` no config.ini). Isso garante que seus arquivos de áudio permaneçam estritamente bit-perfect e intocados por quaisquer instruções de nivelamento de volume via software, ideal para DACs de alta fidelidade e DAPs dedicados.

### 🚀 Mecanismo de Download Resiliente

* **Fila À Prova de Falhas:**  
  Tratamento avançado de exceções no nível da faixa. Se uma única faixa estiver bloqueada geograficamente ou ausente nos servidores (erro 404), o mecanismo a pula normalmente e continua baixando o restante do seu álbum ou playlist sem travar.
 
* **Recuperação e Sincronização de Banco de Dados:**  
  Inclui um mecanismo especializado `--sync-db` para restaurar entradas ausentes em seu banco de dados local escaneando suas pastas de música existentes.
 
* **Sincronização Bidirecional de Playlists (`sync-playlist`):**  
  Um poderoso mecanismo de espelhamento para playlists dinâmicas. Mantenha suas pastas locais perfeitamente sincronizadas com as alterações online (baixando novas faixas e excluindo de forma limpa as que foram removidas). **A v2.0.1 introduz a Lógica Inteligente de Pastas (Smart Folder Logic):**  
  Ao usar `-d .` ou caminhos genéricos, ele cria automaticamente uma subpasta com o nome da playlist, evitando a exclusão acidental de arquivos no seu diretório raiz.
 
* **Tabela Profissional de Faixas Ausentes:** Se o mecanismo de sincronização detectar faixas na sua playlist online que estejam ausentes no seu disco local, ele gera uma tabela ASCII limpa e colorida com Título, Artista e ID para fácil acompanhamento.
 
* **Busca Reversa Inteligente (Reverse Lookup):** Identifica automaticamente arquivos antigos lendo suas tags **ISRC** ou **UPC** e consultando a API do Qobuz para restaurar os IDs corretos no banco de dados.
 
* **Validação Inteligente Prévia de Configuração:** Introduzido na v2.0.3, um sistema de validação inteligente verifica as strings de formatação do seu `config.ini` antes de iniciar qualquer download. Se detectar uma variável não reconhecida, o mecanismo aborta o processo com segurança e usa `difflib` para sugerir inteligentemente a variável correta, evitando exceções `KeyError` silenciosas.

* **Download Segmentado e Remuxagem:** Contorna a limitação de velocidade (throttling) da CDN da Akamai com um mecanismo de download segmentado de alta velocidade e remuxagem automática via FFmpeg.
 
* **Download Multithread:** Downloads simultâneos de faixas para obtenção ultrarrápida de álbuns.
 
* **Interface Limpa para Multithreading:**  
  Alterna de forma inteligente para um sistema de registros estático e limpo exibindo tamanhos precisos de arquivo (MB) durante downloads concorrentes.  
  Isso evita falhas visuais no terminal e conflitos com o mecanismo de letras, enquanto preserva as barras de progresso animadas clássicas para downloads sequenciais (`—-delay`).
 
* **Recuperação de Terminal (Correção do Raw Mode):**  
  Corrigido um bug crítico de interface onde a interrupção do prompt de busca interativo (modo `fun`) com `CTRL+C` deixava o terminal do sistema operacional em um estado quebrado.  
  O mecanismo agora aciona com segurança uma saída graciosa do sistema, restaurando a disciplina de linha padrão do terminal.
 
* **Fallback Inteligente de Qualidade:** Reduz automaticamente para a próxima melhor qualidade disponível caso o nível solicitado seja restrito pelo servidor, garantindo que sua fila de download nunca trave.
 
* **Bypass de Autenticação:** Faça login com segurança usando o **Token de Autenticação** (Auth Token) do seu navegador caso a autenticação padrão por senha esteja bloqueada. Suporta perfeitamente contas Free e Studio.
 
* **Armazenamento Seguro de Credenciais (Keyring do SO):**  
  Diga adeus às senhas em texto puro. Os tokens de autenticação (Qobuz e Genius) são criptografados com segurança e armazenados nativamente no gerenciador de credenciais do seu sistema operacional (Windows Credential Manager, macOS Keychain ou Linux Secret Service).  
  **Recurso da Edição Ultra:** Suporte nativo para ambientes headless (NAS/Docker/WSL). Se o sistema não tiver um daemon de chaveiro seguro, a opção `disable_keyring` permite persistir tokens sem criptografia no `config.ini`; use-a apenas quando necessário e proteja esse arquivo.
 
* **Camuflagem Anti-Banimento (Stealth Spoofing):**  
  WAFs (Web Application Firewalls) modernas bloqueiam requisições de API originadas de scripts headless. Este mecanismo conta com camuflagem criptográfica completa, injetando Client Hints exatos do Windows/Chrome (`Sec-Ch-Ua`, `Sec-Fetch-Site`) para tornar sua sessão indistinguível de um usuário legítimo navegando no Qobuz Web Player, reduzindo significativamente erros 403 e prevenindo banimentos de conta.
 
* **Playlists Sem Limites:** Supera as restrições da API do Qobuz paginando dinamicamente as requisições em blocos, permitindo que você enfileire e baixe playlists massivas sem o gargalo padrão de 50 faixas.
 
* **Retomada Inteligente (Sem Sobrescritas):** Detecta inteligentemente arquivos existentes no seu disco local e os ignora automaticamente. Se o download de uma discografia massiva for interrompido, ele é retomado instantaneamente sem desperdiçar tempo ou largura de banda baixando faixas existentes.
 
* **Mecanismo de Blacklist Anti-Spam:**  
  Filtre automaticamente lançamentos indesejados (“lixo”, como versões em Karaokê, covers instrumentais, álbuns de tributo) ao baixar discografias completas de artistas ou catálogos de gravadoras.  
  Você pode passar um arquivo `.txt` contendo suas palavras-chave personalizadas (ex.: `Karaoke`, `(Live)`, `Original Soundtrack`) pela flag CLI `-b` ou configurá-lo permanentemente no seu `config.ini`.  
  O mecanismo combina dinamicamente o título principal e as tags de versão, garantindo filtragem perfeita antes que um único byte de áudio seja baixado.
 
* **Download em Lote com Estado (Memória em Arquivo de Texto):**  
  Ao baixar filas massivas a partir de um arquivo `.txt`, o mecanismo atua como um banco de dados vivo. Ele valida URLs automaticamente e anexa uma tag `[DONE]` ao lado dos links concluídos diretamente dentro do arquivo de texto.  
  Se sua conexão cair ou você abortar o processo, basta rodar novamente o comando: o mecanismo pulará instantaneamente os links concluídos e continuará a fila exatamente de onde parou.
 
* **Geração Impecável de `.m3u`:** Gera automaticamente arquivos de playlist com caminhos relativos corretos. **A v2.0.1 apresenta um algoritmo robusto de correspondência em 4 etapas** (ID -> ISRC -> Título -> Nome do arquivo) que garante que o arquivo `.m3u` espelhe com precisão a ordem da API, mesmo quando as faixas não possuem prefixos numéricos em seus nomes.
 
* **Mecanismo de Correspondência O(1) Ultrarrápido:** O gerador de playlists agora utiliza indexação de dicionário de alto desempenho. Ele identifica arquivos locais instantaneamente, reduzindo o tempo de processamento de playlists grandes de segundos para milissegundos.

* **Arquivos Temporários Compatíveis com NAS e macOS:** Arquivos temporários de download agora usam um prefixo padrão `~tmp_` em vez de um ponto inicial. Isso impede que sistemas baseados em Unix (macOS, Synology SMB/Samba) apliquem permanentemente atributos de sistema “Oculto” aos seus arquivos de áudio, eliminando a necessidade de comandos de limpeza no terminal.

### 📁 Formatação Avançada e Armazenamento

O Qobuz-DL Ultra permite profunda personalização da estrutura da sua biblioteca usando variáveis.

-  **Suporte a Playlists Reais (Nativo):** Lida perfeitamente com playlists do Qobuz e Last.fm com uma lógica especializada projetada para organização de biblioteca (Resolve a issue #257).
 
-  **Estrutura de Pasta Plana (Flat):** Baixa automaticamente todas as faixas em um único diretório nomeado com o título da playlist, evitando a criação de dezenas de subpastas de álbuns dispersas.
 
-  **Nomenclatura Independente de Posição:** Arquivos de áudio são salvos de forma limpa (ex.: `Artista - Título.flac`) sem prefixos numéricos fixos. Essa abordagem padrão da indústria garante que, se a ordem da playlist mudar online, seus arquivos locais sejam reconhecidos instantaneamente, evitando downloads duplicados em massa.
 
-  **`.m3u` Inteligente Baseado na API:** A ordem de reprodução é garantida por um arquivo `.m3u` gerado dinamicamente que espelha com perfeição a sequência exata ditada pelos servidores do Qobuz, independentemente dos nomes físicos dos arquivos.
 
-  **Gerenciamento Inteligente de Capas:**  
  Elimina o bug de “Conflito de Capas”. O mecanismo gerencia dinamicamente as artes incorporadas, garantindo que cada faixa receba sua capa exclusiva correta sem deixar arquivos `cover.jpg` duplicados na pasta.
 
-  **Capas em Alta Resolução da Apple/iTunes:**  
  Antes de usar a capa do Qobuz, o programa procura a mesma capa na Apple/iTunes (até 10000×10000 px). A busca começa pelo **UPC** do álbum e pelo **ISRC** da faixa (identificadores exatos) e só depois cai para a busca por texto, que exige artista, álbum e título compatíveis.  
  Colaborações são reconhecidas (um álbum de `Cardi B` casa com `Cardi B & Bruno Mars` na Apple), e resultados de karaokê, tributo, instrumental, edições ao vivo ou deluxe diferentes do que você baixou são recusados: sem certeza, a capa do Qobuz é mantida. Se a versão maior passar de 16 MB, o programa desce em cascata (6000, 3000, 1200 e 600 px).
 
-  **Substituição de Modo de Álbum (`--playlist-as-albums`):**  
  *Novo recurso.* Se você usa playlists para buscar músicas específicas, essa flag ignora completamente a lógica de Pasta Plana. O mecanismo vai “explodir” a playlist, direcionando cada faixa para sua respectiva pasta de álbum original usando o seu `folder_format` padrão, mantendo os números de faixa originais e baixando a capa específica de cada álbum.
-  **Variáveis Poderosas:** `folder_format` e `track_format` agora suportam dezenas de novas variáveis (ex.: `{isrc}`, `{barcode}`, `{label}`, `{track_composer}`).
 
*  **Tipo de Lançamento (`{release_type}`):** Identifica automaticamente a categoria de publicação a partir das APIs do Qobuz (ex.: `Album`, `EP`, `Single`), permitindo que você encaminhe dinamicamente downloads para subdiretórios ou use como prefixo de nomenclatura sem impor uma estrutura fixa.

```bash
  *Exemplo de Pasta (Subdiretório):* `folder_format = {release_type}/{album_artist} - {album_title}` ➔ `Album/Daft Punk - Discovery`
  *Exemplo de Pasta (Prefixo):* `folder_format = {release_type} - {album_artist} - {album_title}` ➔ `Single - Gorillaz - Silent Running`
```

- **Tag Explícita (`{explicit}` ou `{ExplicitFlag}`):**  
  Adiciona automaticamente uma tag `[E]` se a faixa ou álbum tiver aviso parental no Qobuz. Se o conteúdo for limpo, a variável permanece vazia sem deixar espaços finais indesejados. **Você pode aplicar isso permanentemente adicionando as variáveis ao seu arquivo `config.ini`, ou temporariamente via CLI usando as flags `-ff` e `-tf`.**

```bash
  *Exemplo de Pasta:* `folder_format = {artist} - {album} {ExplicitFlag}` ➔ `Eminem - The Eminem Show [E]`
  *Exemplo de Faixa:* `track_format = {track_number} - {track_title} {ExplicitFlag}` ➔ `02 - Without Me [E].flac`
```
 
- **Tag de Versão do Álbum (`{version_tag}`):**  
  Adiciona automaticamente a versão do álbum (ex.: Live, Remastered, Deluxe Edition) ao nome da pasta ou faixa. Se o lançamento for uma edição padrão, a variável permanece completamente vazia, evitando espaços ou hifens indesejados.

```bash
  *Exemplo de Pasta (Padrão):* `folder_format = {album_artist} - {album_title}{version_tag}` ➔ `The Sunset Violent`
  *Exemplo de Pasta (Edição Especial):* `folder_format = {album_artist} - {album_title}{version_tag}` ➔ `The Sunset Violent - Live in Heidelberg`
```

- **Roteamento Multi-Disco:** Armazene lançamentos com múltiplos discos em um único diretório ou divida-os usando prefixos personalizáveis (ex.: `CD 01`).
  - **Tags em álbuns com vários discos:** `TRACKNUMBER` vai de 1 ao total do álbum (ex.: 1 a 11) e `TRACKTOTAL` é esse total, então o player mostra a numeração contínua. `DISCNUMBER`/`DISCTOTAL` (e `TPOS` no MP3) continuam gravados, para o player dividir a lista por disco. Vale para álbum, faixa avulsa, playlist e para o comando `tags`; faixa avulsa e playlist consultam o álbum uma vez para saber a ordem das faixas (se a consulta falhar, aquela faixa fica com a numeração por disco). O nome do arquivo dentro de `CD 01`/`CD 02` continua por disco (`CD 02/01 - Temporal`).
 
- **Geração Universal de Playlists:** Arquivos `.m3u` são rigorosamente codificados em UTF-8, garantindo 100% de estabilidade mesmo com caracteres Unicode complexos ou japoneses (Resolve a issue #304).
 
- **Substituição de Caracteres Legados (`legacy_charmap`):** Por padrão, a Edição Ultimate usa caracteres Unicode de largura total elegantes (ex.: `／`) para contornar com segurança as restrições de nomes de arquivos do SO sem perder a estética do título original. No entanto, puristas podem ativar a opção `legacy_charmap = true` no seu `config.ini` para forçar substituições padrão em ASCII (ex.: substituir `/` por `-` ou remover `?`), restaurando a convenção de nomenclatura clássica do qobuz-dl original.

### ❤️ Sincronização Nativa de Favoritos e Menu Interativo

Conecte perfeitamente seus hábitos de escuta móvel com sua biblioteca local offline. Em vez de copiar URLs manualmente, inicie o Modo Interativo (`fun`) para acessar sua conta pessoal do Qobuz com segurança e navegar pelos seus **Álbuns, Faixas, Artistas e Playlists Favoritos** diretamente do terminal.

* **Fluxo de Trabalho Sem Digitação:** Acesse sua biblioteca privada com um único clique sem nunca sair do terminal.
* **Download em Lote Massivo:** Use a `Barra de Espaço` para selecionar múltiplos lançamentos favoritos a partir da interface limpa e minimalista e enfileirar todos para download em segundos ou `t` para selecionar todos os itens de uma vez.
* **Filtro Inteligente de Lançamentos (Mecanismo Heurístico):** Ao buscar a discografia de um artista, o mecanismo executa um algoritmo heurístico local ultrarrápido para categorizar os lançamentos (Álbuns, EPs, Singles, Ao Vivo). Ele apresenta instantaneamente uma interface de caixas de seleção, permitindo filtrar singles ou compilações indesejados antes mesmo do início do download, economizando tempo e armazenamento.

### 🌉 Integração Inteligente com Last.fm e Modo Interativo

Conecte seu mundo do Last.fm ao Qobuz perfeitamente. Baixe suas playlists personalizadas e “Faixas Favoritas” (Loved Tracks) com facilidade.

Para evitar o download de músicas incorretas, este fork utiliza um **Algoritmo Matemático de Correspondência Difusa (Fuzzy Matching)**:
* **Aceitação Automática (> 75%):** Correspondências perfeitas são enfileiradas automaticamente.
* **Pulo Automático (< 60%):** Faixas completamente erradas são ignoradas automaticamente.
* **Seleção Interativa (60% - 74%):** Para correspondências limítrofes, o mecanismo pausa e ativa um prompt interativo permitindo que você aprove ou rejeite manualmente a faixa (`[y/n]`).

### 🛡️ Gerenciamento de Pastas À Prova de Falhas e Retomada Inteligente
Diga adeus a bibliotecas desorganizadas e downloads corrompidos. O baixador agora conta com um sistema dinâmico de 3 estágios de estado de pasta para manter sua biblioteca perfeitamente organizada:
* **`[IN PROGRESS]`**: Pastas são marcadas enquanto o download está em andamento.
* **`[INCOMPLETE]`**: Se você abortar o processo (tratamento suave de `CTRL+C`) ou se algumas faixas forem puladas (ex.: bloqueio regional ou indisponibilidade), a pasta é marcada com segurança como incompleta. 
* **Estado Limpo (Clean State)**: Somente quando um álbum for baixado com **100% de sucesso** a pasta será renomeada para seu estado final limpo (ex.: `Artista - Álbum`).

*Nota: O mecanismo é inteligente o suficiente para retomar downloads diretamente em pastas `[INCOMPLETE]` ou `[IN PROGRESS]` na sua próxima execução!*

## 📥 Instalação e Configuração

> ⚠️ **Requisito:** Você precisa de uma **assinatura ativa** do Qobuz.

<a name="opcao-a-pypi"></a>
### Opção A: 📦 Pacote PyPI (Recomendado para todas as plataformas)
A maneira mais fácil e oficial de instalar a Edição Ultimate. Abra seu terminal e execute:

```bash
pip install qobuz-dl-ultra
```
*Após a instalação, você pode iniciar o programa de qualquer pasta no seu computador digitando simplesmente `qobuz-dl` ou `qdl`.*

<a name="opcao-b-codigo-fonte"></a>
### Opção B: Código-fonte Python (Avançado)
Clone este repositório e instale as dependências necessárias:

```bash
git clone https://github.com/kaduvercosa/qobuz-dl-ultra.git
cd qobuz-dl-ultra
pip3 install -r requirements.txt
```

> **Recursos opcionais:** o `requirements.txt` e o `pip install qobuz-dl-ultra` trazem só o núcleo. Instale o que precisar: `pip install "qobuz-dl-ultra[covers]"` (Pillow), `[speed]`, `[watch]`, `[genius]`, `[duplicates]`, `[analyze]`, `[enhanced]` ou `[all]` para tudo. O extra `[enhanced]` instala aiolimiter, que protege chamadas à API contra rajadas; o layout de terminal continua usando o renderer próprio, responsivo e compatível com a-Shell. O comando `tags` **não depende do Pillow** para comparar capas (lê as dimensões do próprio JPEG/PNG); o Pillow só é usado para recompactar uma capa acima do limite de 16 MB do FLAC. No **a-Shell**, não instale extras: use o que já vem instalado. Rode `qobuz-dl doctor` para ver o que está disponível.

*Execute o programa usando:* `python -m qobuz_dl`

<a name="opcao-c-docker"></a>

### Opção C: 🐳 Uso com Docker (NAS e Servidores Caseiros)

A imagem Docker já traz tudo pronto (Python, FFmpeg e os extras de capas, velocidade, letras e análise). O `docker-compose.yml` do projeto automatiza as partes que mais dão erro: permissões das pastas, Keyring e pasta de download. **Use sempre o Compose**; não é preciso criar nenhum arquivo de configuração antes, porque o assistente do próprio programa abre na primeira execução.

#### Passo 1: Instalar o Docker

| Sistema | O que instalar |
|:--- |:--- |
| Windows e macOS | [Docker Desktop](https://docs.docker.com/desktop/). Abra o programa e espere o ícone indicar que o Docker está rodando. |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) com o plugin Compose. Depois rode `sudo usermod -aG docker $USER` e saia e entre na sessão de novo, para usar `docker` sem `sudo`. |
| NAS (Synology, QNAP, Unraid) | Instale o pacote de contêineres do sistema (Container Manager, Container Station ou o Docker do Unraid) e use o terminal por SSH para os comandos abaixo. |

Confirme que funcionou (os dois comandos devem mostrar uma versão):

```bash
docker --version
docker compose version
```

#### Passo 2: Obter os arquivos do projeto

```bash
git clone https://github.com/kaduvercosa/qobuz-dl-ultra.git
cd qobuz-dl-ultra
```

Para o Docker bastam estes dois arquivos, na mesma pasta (a imagem em si é baixada da internet):

```
qobuz-dl-ultra/
├── docker-compose.yml
└── docker/
    └── iniciar.py
```

Todos os comandos seguintes são executados **dentro dessa pasta** (a que contém o `docker-compose.yml`).

#### Passo 3: Primeira execução (configuração guiada)

```bash
docker compose run --rm qobuz-dl dl "https://play.qobuz.com/album/..."
```

Na primeira vez o Docker baixa a imagem (pode levar alguns minutos) e abre o **assistente de configuração**. Responda assim:

- **E-mail e token:** o e-mail da sua conta Qobuz e o token do navegador (veja [Como obter seu Auth Token](#-como-obter-seu-auth-token)).
- **Keyring (cofre do sistema):** qualquer resposta serve. Dentro do container ele fica desativado e o token é guardado no `config.ini` com permissão `600`.
- **Pasta de download:** **aperte Enter**. O `iniciar.py` ajusta a pasta automaticamente para `/home/qobuz/QobuzDownloads`, que é a pasta ligada à pasta `musicas` do seu computador.
- **Letras, idioma, qualidade e demais perguntas:** responda como preferir (Enter aceita o padrão).

Terminado o assistente, o download começa. Os arquivos aparecem em `./musicas` e a configuração em `./config`, ambas criadas sozinhas ao lado do `docker-compose.yml`.

> Se o assistente avisar que o login falhou, o token está errado ou expirou. Rode `docker compose run --rm qobuz-dl -r` para refazer a configuração.

#### Passo 4: Uso diário

O comando é sempre o mesmo; só muda o que vem depois de `qobuz-dl`:

```bash
# Baixar um álbum, faixa, artista ou playlist
docker compose run --rm qobuz-dl dl "URL"

# Reescrever as tags da sua biblioteca (sem pasta, usa a pasta de downloads)
docker compose run --rm qobuz-dl tags

# Diagnóstico da instalação (config, bancos de dados, pasta de downloads)
docker compose run --rm qobuz-dl doctor

# Refazer a configuração do zero
docker compose run --rm qobuz-dl -r
```

Dentro do container, a sua pasta `musicas` é sempre `/home/qobuz/QobuzDownloads`. Ao passar caminhos para os comandos, use esse caminho, não o do seu computador. Exemplo: `docker compose run --rm qobuz-dl inspect /home/qobuz/QobuzDownloads/Album/01.flac`.

#### O que acontece automaticamente a cada comando

1. **Serviço `permissoes`:** ajusta o dono das pastas `config` e `musicas` para o usuário da imagem (`qobuz`).
2. **Script `docker/iniciar.py`:** testa de verdade se as duas pastas aceitam escrita; abre o assistente se não houver configuração; e, se necessário, corrige o `config.ini` (desativa o Keyring e aponta a pasta de download para a pasta montada), guardando antes uma cópia em `config/config.ini.bak`.
3. **Seu comando** é executado.

Se algo estiver errado, o processo para com uma mensagem explicando o motivo, em vez de baixar os arquivos para dentro do container, onde seriam perdidos.

#### Permissões: o que precisa estar certo

- **Usuário sem privilégios.** O container não roda como administrador (root). Ele roda como o usuário `qobuz`, normalmente de UID 1000. Para conferir o número real: `docker compose run --rm --entrypoint id qobuz-dl`.
- **As pastas do computador precisam ser graváveis por esse usuário.** O serviço `permissoes` faz isso sozinho. O equivalente manual, caso precise, é:
  ```bash
  sudo chown -R 1000:1000 config musicas
  ```
- **No computador, os arquivos aparecem pertencendo ao UID 1000.** Se você usa outro usuário, pode precisar de `sudo` para editar ou apagar arquivos de `config` e `musicas`. O `config.ini` tem permissão `600`: só o dono lê, já que guarda seu token.
- **Biblioteca antiga de outro usuário.** Por padrão só a pasta `musicas` em si muda de dono (rápido). Se o comando `tags` precisa alterar arquivos que já existiam e pertencem a outro usuário, edite o `docker-compose.yml`: no serviço `permissoes`, troque `chown qobuz:qobuz /home/qobuz/QobuzDownloads` por `chown -R qobuz:qobuz /home/qobuz/QobuzDownloads`. Em bibliotecas grandes isso pode demorar na primeira vez.
- **NAS com SMB/NFS e Docker Desktop.** O `chown` pode não ter efeito. Nesse caso libere leitura e escrita na pasta compartilhada para o usuário usado pelo Docker; o teste de escrita do `iniciar.py` mostra um erro claro se ainda faltar permissão.
- **Fedora, RHEL e CentOS (SELinux).** Se aparecer “permission denied” mesmo com o dono certo, acrescente `:z` ao final das linhas de volume do `docker-compose.yml`, por exemplo `./musicas:/home/qobuz/QobuzDownloads:z`.

#### Usar outras pastas (por exemplo, a pasta de músicas do NAS)

No `docker-compose.yml`, nos **dois** serviços (`permissoes` e `qobuz-dl`), troque só o começo das linhas de volume (o que vem antes dos `:`):

```yaml
volumes:
  - ./config:/home/qobuz/.config/qobuz-dl
  - /volume1/musica:/home/qobuz/QobuzDownloads
```

O que vem depois dos `:` **não muda**.

#### Atualizar e fazer backup

```bash
docker compose pull          # baixa a imagem mais nova
cp -r config config-backup   # backup da configuração (contém seu token)
```

#### Usar uma imagem construída do seu código

```bash
docker build -t qobuz-dl-ultra:local .
```

Depois, no `docker-compose.yml`, troque `ghcr.io/kaduvercosa/qobuz-dl-ultra:latest` por `qobuz-dl-ultra:local` nas duas linhas `image:`.


#### Solução de problemas

| Mensagem ou sintoma | Causa e solução |
|:--- |:--- |
| `Sem permissão para gravar em ...` | A pasta do computador não aceita escrita do usuário `qobuz`. Veja a seção de permissões acima. |
| `O assistente precisa de um terminal` | O comando foi rodado sem terminal (por exemplo, com `-T` ou por agendador). Rode uma vez normalmente para configurar. |
| `401 Unauthorized` | Token inválido ou expirado. Rode `docker compose run --rm qobuz-dl -r` e informe um token novo. |
| `O config.ini existe, mas sem o token` | O arquivo foi copiado de outra máquina com Keyring. Rode `docker compose run --rm qobuz-dl -r`. |
| Os arquivos não aparecem em `musicas` | Pasta de download fora da pasta montada. O `iniciar.py` corrige sozinho; rode `docker compose run --rm qobuz-dl doctor` para conferir. |
| `docker: command not found` ou `Cannot connect to the Docker daemon` | O Docker não está instalado ou não está rodando (abra o Docker Desktop ou inicie o serviço). |

> **🔒 Segurança:** o `config/config.ini` contém seu token de acesso em texto puro (protegido apenas pela permissão `600`). Nunca envie a pasta `config` para o GitHub nem a compartilhe. Acrescente `config/` e `musicas/` ao seu `.gitignore`.


### ⚙️ Configuração e Caminhos Personalizados
Se você deseja definir uma pasta de download personalizada, pode editar seu arquivo `config.ini` e usar a chave `directory`. Caminhos absolutos e o operador `~` (para macOS/Linux) são totalmente suportados!

> **💡 Dica para usuários que estão atualizando:** Para acessar as opções de configuração mais recentes (como `embed_lyrics` e `multi_value_tags`), basta executar `qobuz-dl -r` para redefinir e gerar um novo arquivo `config.ini`, ou adicioná-las manualmente na seção `[qobuz]`.

```ini
[qobuz]
directory = ~/Music/Qobuz_Lossless

# Defina como 'true' se estiver executando em um servidor headless (NAS/Docker/WSL) 
# para salvar tokens sem criptografia no config.ini em vez do Keyring do SO.
disable_keyring = false

# Defina como 'true' para restaurar substituições clássicas de caracteres ASCII
legacy_charmap = false

# Defina como 'true' para desativar a geração de arquivos .lrc externos
no_lrc_files = true

# Defina como 'true' para restaurar substituições clássicas de caracteres ASCII (ex.: substituir '/' por '-')
legacy_charmap = false

# Defina como 'true' para desativar a geração de arquivos .lrc externos (as letras serão incorporadas apenas nas tags FLAC/MP3)
no_lrc_files = true

# Defina como 'true' para desativar estritamente tags de volume ReplayGain para reprodução bit-perfect em hardware
no_replaygain_tag = true

# Defina como 'true' para desativar a gravação da tag não padrão "QOBUZ ALBUM URL" nos arquivos
no_album_url_tag = true

# Defina como 'true' para desativar a extração de metadados de música clássica
no_conductor_tag = true
no_ensemble_tag = true
no_work_tag = true

# Defina como 'true' para não gravar créditos de produção (LYRICIST, PRODUCER, MIXER, ENGINEER, ARRANGER)
no_credits_tag = true

# Defina como 'true' para gravar os IDs do MusicBrainz (MUSICBRAINZ_TRACKID, MUSICBRAINZ_ALBUMID,
# MUSICBRAINZ_ARTISTID). So grava quando titulo, versao, album e artista conferem; custa cerca de 1 s
# por faixa. Tambem pode ser ativado por download com a flag --musicbrainz
musicbrainz = true
```
*(Nota: Se você estiver atualizando de uma versão anterior, a chave legada `default_folder` ainda é totalmente suportada para compatibilidade retroativa.)*

> **🔒 Nota de Segurança:** Com o Keyring ativo, `auth_token` e `genius_token` são migrados para o gerenciador de credenciais do sistema. Com `disable_keyring = true`, eles permanecem sem criptografia no `config.ini`; a permissão `0600` restringe quem pode abrir o arquivo, mas não criptografa seu conteúdo.

### 🔑 Como obter seu Auth Token
Como o Qobuz bloqueou logins diretos por senha para aplicativos de terceiros, você precisa fornecer o Token de Autenticação do seu navegador durante a configuração inicial. Veja como encontrá-lo facilmente:
1. Abra o [Qobuz Web Player](https://play.qobuz.com) no seu navegador e faça login.
2. Pressione `F12` para abrir as Ferramentas de Desenvolvedor (Developer Tools).
3. Vá para a aba **Application** (Chrome/Edge) ou **Storage** / **Armazenamento** (Firefox).
4. Na barra lateral esquerda, expanda **Local Storage** (Armazenamento Local) e clique em `https://play.qobuz.com`.
5. Na lista de chaves, procure por **`localuser`**.
6. Na parte inferior do painel (ou expandindo o valor JSON), localize a string **`token`**.
7. Abra o terminal e force o assistente de login executando `qobuz-dl -r` (ou `--reset`). Quando o prompt aparecer, selecione o método Auth Token e cole sua sequência alfanumérica!

## 💻 Uso e Exemplos Rápidos

**Ajuda no terminal.** `qobuz-dl` (sem argumentos) e `qobuz-dl -h` abrem a tela inicial, com os comandos, as flags globais e um início rápido. Para um comando específico use `qobuz-dl <comando> -h` (por exemplo `qobuz-dl dl -h`): a ajuda vem nos mesmos painéis da tela inicial — **Uso** (com um exemplo pronto),  

**Argumentos** e **Opções**. Nos comandos de download (`dl`, `interactive`, `lucky`) as opções vêm separadas por assunto, na ordem em que você as usa (busca → qualidade → pastas e nomes → letras → tags → créditos, depois capa, discos e saída), e cada opção fica separada da seguinte por uma linha horizontal. A ajuda do `dl`/`interactive` termina com um painel  

**Chaves de formato** que lista as variáveis aceitas por `-ff`, `-fbff` e `-tf`. O layout se adapta à tela: com 96 colunas ou mais as opções ficam em duas colunas; em telas estreitas (celular, a-Shell no iPad em pé) viram cartões empilhados e nenhuma linha passa da largura. Sem cores quando a saída não é um terminal (por exemplo `> ajuda.txt`).

```text
qobuz-dl dl [opções] <URL_OU_ARQUIVO>
qobuz-dl lucky [opções] <BUSCA>
qobuz-dl tags [opções] [PASTA]
qobuz-dl scan [opções] [PASTA]
qobuz-dl inspect [opções] [ARQUIVO_OU_PASTA]
qobuz-dl <comando> -h        # ajuda completa do comando
```

**Inspecionar uma faixa.** `qobuz-dl inspect` mostra tudo sobre um arquivo de áudio local, em seções: dados técnicos (codec, qualidade, bitrate real, encoder, assinatura MD5 do FLAC), tags agrupadas por assunto (identificação, faixa e disco, códigos, IDs do Qobuz, ReplayGain — o que não couber vai para "Outras tags", nada some), capas embutidas (tamanho, se é quadrada), letras (embutida, sincronizada, `.lrc` ao lado), um diagnóstico com os mesmos avisos do `tags --report` e a checagem de autenticidade. Funciona com FLAC, MP3, M4A/ALAC, Ogg Vorbis e Opus.

```bash
qobuz-dl inspect                              # abre o navegador na pasta do config.ini
qobuz-dl inspect ~/Music/faixa.flac           # direto, sem navegador
qobuz-dl inspect ~/Music/faixa.flac --completo  # + integridade e volume/pico (ffmpeg)
qobuz-dl inspect ~/Music/faixa.flac --sem-espectro  # sem a análise espectral (mais rápido)
qobuz-dl inspect ~/Music/Album --resumo       # uma entrada por faixa + totais da pasta
qobuz-dl inspect ~/Music/faixa.flac --json    # só JSON, para scripts
qobuz-dl inspect ~/Music/faixa.flac --comparar  # o que o `tags` mudaria (simulação; precisa de login)
```



**Sincronização Bidirecional de Playlist:**
*(Dica: Adicione `-y` para ignorar os avisos de confirmação. A flag `-d` opera com segurança, criando automaticamente uma subpasta para a playlist).*

```
python -m qobuz_dl sp "URL" -d "C:\Caminho\Para\Pasta\Local\Da\Playlist"
```

**Explodir Playlists em Álbuns:**
Por padrão, as playlists são baixadas em uma única pasta plana. Use esta flag se você usa playlists como uma “ferramenta de descoberta” e deseja que o mecanismo encaminhe dinamicamente cada faixa para sua respectiva pasta de álbum original, com sua arte de capa específica e metadados originais da faixa.

```
python -m qobuz_dl dl "URL_DA_PLAYLIST" --playlist-as-albums
```

**Download em Massa / Lote (Retomada Inteligente):**
Tem uma lista enorme de lançamentos para baixar? Crie um arquivo de texto comum (ex.: `list.txt`), cole suas URLs do Qobuz **e do Last.fm** nele (uma por linha) e passe-o para o mecanismo. O analisador inteligente baixará automaticamente seus links do Qobuz e roteará perfeitamente as playlists do Last.fm pelo mecanismo de Fuzzy Matching para processar toda a sua fila de uma só vez!

*Recurso da Edição Ultra:* O arquivo de texto atua como um banco de dados vivo. Assim que um lançamento ou playlist completa é baixado com sucesso, o mecanismo anexa uma tag `[DONE]` ao lado da URL no arquivo. Se sua conexão cair ou você interromper o processo (`CTRL+C`), basta executar exatamente o mesmo comando novamente e o mecanismo pulará instantaneamente os links concluídos, retomando perfeitamente de onde parou.

```
python -m qobuz_dl dl list.txt
```

**Blacklist de Discografia e Modo Anti-Spam:**
Está baixando a discografia completa de um artista, mas quer evitar gastar espaço com versões de Karaokê, Tributo ou Instrumentais? Crie um arquivo de texto (ex.: `blacklist.txt`) contendo as palavras-chave indesejadas (uma por linha) e passe-o para o mecanismo. Ele inspecionará automaticamente cada lançamento e pulará o conteúdo indesejado!
*(Dica: Você pode definir `blacklist = blacklist.txt` no seu `config.ini` para tornar isso automático em cada download).*

```
python -m qobuz_dl dl https://play.qobuz.com/artist/123456 -b blacklist.txt
```

**Modo Anti-Ban Supremo (Camuflagem + Delay):**
Embora o mecanismo mascare nativamente sua pegada digital (Stealth Spoofing) para simular um navegador Chrome real, baixar 100 faixas em 10 segundos ainda é fisicamente impossível para um ser humano e pode acionar banimentos baseados em volume. Use este comando para grandes discografias para desativar o multithreading e adicionar um intervalo forçado entre as faixas, garantindo a máxima segurança para sua conta.

```
python -m qobuz_dl dl <URL> --delay 1
```

**Forçar Encartes e Créditos (Substituição de Configuração):**
Se você definiu `no_credits = true` no seu `config.ini` para manter suas pastas limpas, pode substituir temporariamente esse comportamento para forçar a geração do Encarte Digital e do Tracklist.txt para uma obra-prima específica.

```
python -m qobuz_dl dl <URL> --with-credits
```

**Análise de Múltiplas Tags e Intérpretes:**
Use a flag `--multi-tags` para garantir que faixas complexas com múltiplos artistas e compositores sejam divididas em campos limpos e individuais nas tags de áudio.

```
python -m qobuz_dl dl "URL" --multi-tags
```

**Artistas com vírgula no nome:** nomes como `Tyler, The Creator` ou `Earth, Wind & Fire` são reconhecidos e **não** são divididos em dois artistas (nem com `--multi-tags`). Há uma lista embutida e, além dela, qualquer artista que a API do Qobuz entregue como entrada estruturada com vírgula no nome é protegido automaticamente. Para incluir outros, adicione na seção `[qobuz]` do `config.ini` (separados por `;`):

```ini
artists_with_comma = Tyler, The Creator; Earth, Wind & Fire
```

**Modo Apenas Metadados e Encarte:**
Quer completar os metadados da sua biblioteca sem baixar gigabytes de áudio? Este comando busca apenas a arte da capa, gera o encarte com lista de faixas/créditos, baixa os Goodies em PDF oficiais e ignora com segurança todas as faixas de áudio.

```
python -m qobuz_dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb --booklet-only
```

**Modo de Pasta Minimalista (Sem arquivos .lrc externos):**
Baixa o álbum e injeta as letras sincronizadas exclusivamente nos metadados do FLAC/MP3, mantendo suas pastas completamente limpas de arquivos de texto externos.

```
python -m qobuz_dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb --no-lrc-files
```

**Roteamento Avançado de Discografia:**
Salve múltiplos discos de um lançamento em uma única pasta em vez de dividi-los.

```
python -m qobuz_dl dl https://play.qobuz.com/artist/2038380 --multiple-disc-one-dir
```

**Modo Interativo Last.fm (Modo Fun):**
*(Dica: No modo interativo, use `Espaço` para selecionar múltiplos álbuns para baixar de uma vez!)*

```
python -m qobuz_dl fun -l 10
```
**Modo Audiófilo Purista (Sem ReplayGain):**
Baixe uma faixa mantendo o arquivo estritamente bit-perfect, sem gravar quaisquer tags de nivelamento de volume (útil para DSPs e DAPs de hardware).

```
python -m qobuz_dl dl "URL" --no-replaygain-tag
```

### 🗄️ Gerenciamento de Banco de Dados e Biblioteca
A Edição Ultra inclui poderosos gerenciadores de biblioteca local para acompanhar seus downloads, evitar duplicatas e corrigir seus metadados retroativamente.

* **Sincronização Inteligente de Biblioteca (`--sync-db`):**
  Já possui uma biblioteca local de FLACs baixados? Não precisa começar do zero. Execute este comando para realizar uma *Busca Reversa* no seu diretório de downloads. O mecanismo escaneará seus arquivos existentes e os injetará automaticamente no banco de dados local para evitar downloads duplicados no futuro.

  ```
  python -m qobuz_dl --sync-db
  ```
  *(Nota: Você também pode especificar um caminho personalizado para escanear, ex.: `--sync-db "/caminho/para/suas/musicas"`)*

* **Sincronização Dinâmica de Playlists (`sync-playlist` / `sp`):**
  Playlists são entidades vivas. Em vez de baixar novamente uma playlist inteira toda vez que o autor adiciona uma nova música, aponte este comando para a sua pasta existente. Ele escaneará as tags locais, consultará a API do Qobuz e calculará o delta exato: baixando apenas as faixas ausentes, excluindo de forma limpa as removidas (junto com seus respectivos arquivos `.lrc`) e regenerando a ordem no `.m3u`.

  ```
  python -m qobuz_dl sp "URL_DA_PLAYLIST" -d "/caminho/para/sua/pasta/local"
  ```

* **Tagger Retroativo de Letras (`lyrics`):**
  Tem uma biblioteca de músicas local existente que não possui letras sincronizadas? O novo comando `lyrics` funciona como um mecanismo autônomo de metadados. Ele varre recursivamente qualquer diretório local, detecta arquivos FLAC/MP3 sem letras e as injeta de forma inteligente nos arquivos de áudio usando letras nativas do Qobuz (e quando disponível também injeta a tradução) tendo como Fallback o LRCLIB e a API do Genius sem baixar novamente nenhuma música.
  Atualmente o comando lê a pasta raiz configurada no ’config.ini’

  ```
  python -m qobuz_dl lyrics "/caminho/para/sua/pasta/local/de/musica"
  ```

  Por padrão os arquivos são processados **um de cada vez**. Para acelerar bibliotecas grandes use `--max-workers N` (ou `max_workers` no `config.ini`, o mesmo valor dos downloads): até **4 arquivos ao mesmo tempo** — acima disso o limite é 4, porque os provedores de letra (Musixmatch, LRCLIB, Genius) limitam requisições. O relatório final continua na ordem dos arquivos; só as linhas “Analisando” aparecem misturadas durante a execução.

  ```
  python -m qobuz_dl lyrics --max-workers 3 "/caminho/para/sua/pasta/local/de/musica"
  ```

* **Correção Retroativa de Tags (`tags`):**
  Arquivos já baixados com tags erradas, incompletas ou no formato antigo? O comando `tags` varre a pasta **no dispositivo** (o banco de dados não é consultado), lê o ID Qobuz gravado em cada arquivo FLAC/MP3 e regrava as tags de metadados (artista, álbum, compositor, gênero, data, faixa/disco, ISRC, IDs, comentário técnico) exatamente como um download novo faria, respeitando as opções `no_*_tag` do `config.ini`. Letras (`.lrc`) não são alteradas. Você escolhe o formato dos campos multivalorados em cada execução; sem flag, vale o `multi_value_tags` do `config.ini`. Se nenhuma pasta for informada, o comando usa a pasta padrão do `config.ini` (a mesma dos downloads).

  O `tags` também **troca a capa embutida** pela versão em alta resolução da Apple/iTunes, usando as mesmas regras de validação dos downloads (UPC/ISRC primeiro, depois busca por texto com artista, álbum e título compatíveis). Quando a capa é trocada, a origem fica registrada no comentário da faixa (`Capa: Apple/iTunes`). Quando a Apple não tem uma correspondência segura, a capa que já estava no arquivo é preservada e o motivo aparece no log (por exemplo, `artista incompatível` ou `título completo da faixa incompatível`). O resumo final conta `Capas Apple embutidas`, `Capas Apple não encontradas` e `Falhas na etapa Apple`; os arquivos temporários de capa (`.apple-cover-*.jpg`) são apagados logo após cada arquivo.

  **Diff das tags alteradas e títulos com sufixo de edição.**  
  Quando as tags de um arquivo mudam, o log lista logo abaixo de `↳ Tags: alteradas` exatamente o que mudou (`+` tag nova, `-` removida, `~ antes → depois`; no `COMMENT`, só as linhas que mudaram).  
  Se a capa atual é mantida, as linhas de capa do comentário (`Capa embutida:`/`Capa:`) são preservadas em vez de apagadas. Na busca por texto da Apple, sufixos que só indicam a edição do álbum — `(Bonus Track)`, `(Deluxe)`, `(Explicit)` — não impedem mais a correspondência (ex.: `Fim Do Mundo` ↔ `Fim do Mundo (Bonus Track)` em `LOBOS (Deluxe)`).  
  Marcadores de outra gravação — `(Ao Vivo)`/`(Live)`, `(Acoustic)`, `(Demo)`, `(Remix)`, `(Instrumental)` — continuam sendo rejeitados, a não ser que o álbum pedido seja do mesmo tipo (um álbum ao vivo na Qobuz aceita a faixa ao vivo da Apple).

  **Busca da capa dentro do álbum certo.** A busca por faixa não decide pelo primeiro resultado: quando ela não traz um candidato idêntico, o `tags` localiza o álbum pedido na Apple (mesmo artista, mesmo nome, mesmo tipo de versão), lista as faixas dele e confere a faixa lá dentro. Todos os candidatos de todas as etapas e países são avaliados e a capa só é escolhida no fim, preferindo o álbum de título idêntico ao pedido (ex.: `Lobos` em vez de `LOBOS (Deluxe)`). Só um candidato perfeito (artista, álbum e título idênticos) encerra a busca mais cedo. As respostas de álbum são reaproveitadas durante a execução, então um álbum inteiro não é consultado uma vez por faixa.

  **O que alterar: só tags, só capas ou os dois.** Por padrão o `tags` faz as duas coisas. Para escolher:

| Opção | Tags | Capa | Consulta a Apple? |
|:--- |:--- |:--- |:--- |
| *(nenhuma)* ou `--both` | _regrava_ | _troca quando validada_ | _SIM_ |
| `--tags-only` | _regrava_ | _**não é tocada** (a embutida e as linhas de capa do comentário ficam como estão)_ | _**NÃO**_ |
| `--covers-only` | _**não é tocada**_ | _troca quando validada e de resolução maior (a origem/tamanho vai para o comentário e para `COVER-SOURCE`)_ | _SIM_ |

  As três opções são excludentes entre si. Em `--tags-only` o log mostra `Modo: SOMENTE TAGS`, a linha `Capa` aparece como `não tocada` e o resumo omite as contagens da Apple.

  ```bash
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --multi-tags
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --no-multi-tags
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --tags-only
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --covers-only
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --both
  ```

  **Simular antes (`--dry-run`) e desfazer (`--undo`).** O `tags` reescreve muitos arquivos de uma vez, então há duas redes de segurança:

  * `--dry-run` **simula**: consulta o Qobuz e a Apple, grava numa cópia temporária de cada arquivo e mostra o diff real (`[simulação]`), sem tocar na sua pasta. O resumo final diz quantos arquivos *seriam* atualizados e quais campos mais mudariam.
  * Em toda execução de verdade, antes de gravar cada arquivo o `tags` guarda os **metadados originais** (tags e capa, byte a byte; o áudio nunca é copiado) em `<pasta do config>/tags_undo/`. São mantidas as 5 últimas execuções. `--no-undo` desliga o registro.

  ```bash
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --dry-run   # só mostra o que seria feito
  python -m qobuz_dl tags --undo-list                                            # execuções que dá para desfazer
  python -m qobuz_dl tags --undo                                                 # desfaz a mais recente
  python -m qobuz_dl tags --undo 20261013_190000                                 # desfaz uma execução específica
  python -m qobuz_dl tags --undo --dry-run                                       # simula o desfazer
  ```

  O `--undo` só restaura arquivos que continuam como o `tags` os deixou; se algum foi alterado depois por outro programa, ele é pulado e avisado (`--undo-force` restaura mesmo assim). O `--undo` não precisa de login. Um registro de capa pesa o tamanho das capas trocadas (guardadas uma vez por álbum).

  **Descobrir nomes com vírgula (`--suggest-names`).** Artistas como `Tyler, The Creator` ou `Crosby, Stills, Nash & Young` têm vírgula no nome e seriam separados em vários artistas. O comando varre a sua pasta, procura pares de nomes que *sempre* aparecem juntos (em pelo menos 3 arquivos) e imprime a linha pronta para colar no `config.ini`. Não grava nada nos arquivos e não precisa de login:

  ```bash
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --suggest-names
  ```

  **Escolha do modo.** Rodando o `tags` num terminal, sem `--tags-only`, `--covers-only` ou `--both`, ele pergunta o que atualizar (tags e capas / só tags / só capas; Enter = tags e capas). Em scripts, cron ou pipes (sem terminal interativo) não pergunta nada e usa o padrão.

  **Retomar e rodar só o que mudou (`--resume`, `--incremental`).** Em bibliotecas grandes uma execução pode levar horas e ser interrompida (CTRL+C, rede, bateria). O `tags` anota, por pasta e por modo, cada arquivo que terminou de processar:

  * `--resume` continua a última execução interrompida, pulando o que ela já tinha feito. Se a execução anterior terminou normalmente, avisa e processa tudo.
  * `--incremental` pula tudo o que já foi processado e não mudou desde então: só pega arquivos novos ou alterados por outro programa.
  * Sem nenhuma das duas, processa tudo de novo (é assim que se tentam outra vez as capas da Apple que não foram achadas). Arquivos com erro, falha de download ou de gravação da capa nunca são marcados e são tentados na próxima vez. A simulação (`--dry-run`) só lê esse estado, nunca grava.

  **Relatório da biblioteca (`--report`).** Para saber *onde* rodar o `tags` antes de rodá-lo, o comando varre a pasta (só leitura, sem login, sem rede) e gera um relatório com os problemas por álbum e por faixa: sem capa, capa pequena (< 500 px) ou não quadrada, sem ISRC, ID Qobuz ausente ou inválido, sem artista do álbum, sem data, sem número da faixa. É HTML por padrão (abre em qualquer navegador, com filtro por álbum); se o nome terminar em `.csv`, sai uma planilha:

  ```bash
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --report                  # relatorio_biblioteca.html
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --report problemas.csv
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --resume
  python -m qobuz_dl tags "/caminho/para/sua/pasta/local/de/musica" --incremental
  ```

  **Capa da Apple: regras extras.** A capa da Apple só substitui a atual se for maior *e* quadrada (tolerância de 3%); capas retangulares são mantidas e aparecem no resumo como `mantida (a da Apple não é quadrada)`. Se a capa nova passar do limite de 16 MB de um bloco FLAC, ela é recomprimida com o Pillow ou, se o Pillow não estiver disponível (como no a-Shell), com o `ffmpeg`.

* **Limpar Banco de Dados (`-p`, `--purge`):**
  Se você precisar recomeçar do zero, limpar seu histórico de downloads ou corrigir um estado corrompido, pode apagar instantaneamente o banco de dados local com um único comando.

  ```bash
  python -m qobuz_dl --purge
  ```

* **Estatísticas do Usuário (`stats`):**
  Curioso sobre seus hábitos de download? Este comando consulta instantaneamente seu banco de dados SQLite local para exibir estatísticas dos seus downloads, incluindo o número total de artistas únicos baixados e uma lista alfabética completa da sua biblioteca.

  ```bash
  python -m qobuz_dl stats
  ```

### 🗂️ Catálogo Local, Scan e Sync de Favoritos (novo na 2.6)

Inspirado no libsync, agora existe um catálogo local (`library.db`, ao lado do `config.ini`) que responde: *”o que eu tenho na conta vs. o que eu tenho no disco?”*.

```bash
qobuz-dl sync-favorites                  # mostra o diff (novos/removidos) e atualiza o catálogo
qobuz-dl sync-favorites --download-new   # baixa o que foi favoritado desde a última vez
qobuz-dl sync-favorites --download-missing --limit 20 -y
qobuz-dl sync-favorites --download-new --every 60   # modo contínuo (NAS/servidor)
qobuz-dl sync-favorites --dry-run        # só simula

qobuz-dl scan "/musica"                  # casa pastas do disco com o catálogo (offline)
qobuz-dl scan --dry-run --json rel.json  # classifica sem gravar e salva o relatório

qobuz-dl library                         # status do catálogo
qobuz-dl library missing                 # favoritos ainda não baixados
qobuz-dl library history                 # últimas sincronizações
qobuz-dl library reconcile [PASTA] [--fix] # sentinelas do disco ⇄ catálogo
qobuz-dl library reset-stuck             # destrava álbuns presos após um CTRL+C
qobuz-dl library unmark <ID>             # desmarca um álbum (e remove a sentinela)

qobuz-dl artists                         # tabela A-Z com checkbox; confirmar cria a playlist NOME EM MAIÚSCULAS
qobuz-dl artists --modo sorteio          # sorteia um artista; prioriza os que têm lançamentos pendentes
qobuz-dl artists --sem-sincronizar       # usa o catálogo local sem consultar o Qobuz
qobuz-dl --quiet artists --json          # exporta artistas seguidos em JSON para Atalhos

qobuz-dl doctor [--json]                 # diagnóstico: ambiente, config, keyring, bancos
```

**Revisão de artistas.**  

`artists` abre uma TABELA no mesmo estilo do modo interativo (colunas ARTISTA, NOVOS, STATUS, CONFERIDO EM e PERFIL; linha atual com `➤` e fundo de destaque; `◇` pendente, `*` marcado agora, `✔` conferido, `↺` a desfazer; em tela estreita vira cartões).  

Teclado: setas/PgUp/PgDn/Home/End movem;  

**Espaço** (ou `x`) marca;  
**A** marca todos os pendentes visíveis (de novo, desmarca);  
**D** (ou →) abre a tela do artista;  
**O** abre o perfil no navegador (no a-Shell usa o comando nativo `open`);  
**S** alterna a ordem A-Z / novos primeiro;  
**/** busca (ignora acentos);  
**F** mostra só os pendentes;  
**Enter** confirma;  
**Q** sai (com marcados sem confirmar, pede um segundo `q`).  

Toque/clique: tocar no nome abre a tela do artista; tocar na caixa, no STATUS ou nas bordas marca; tocar em `[abrir]` abre o perfil.   

**Tela do artista:** mostra o estado, o link do perfil e a tabela dos lançamentos pendentes (LANÇAMENTO, TIPO, DATA, com rolagem), com `O` para abrir o perfil, Espaço para marcar e Esc/Q/← para voltar.  
**Confirmar:** cada artista marcado ganha no Qobuz uma playlist **vazia com o nome dele em MAIÚSCULAS** (por exemplo `SELENA GOMEZ`), pronta para você adicionar as músicas dele, e a conferência é gravada no banco junto com o id da playlist.  

A playlist é procurada primeiro pelo id guardado, depois pelo nome na conta, e só criada se não existir (sem duplicar; se a listagem falhar, confia no id guardado).  
Se a criação falhar, o artista continua pendente e marcado.  

**Desfazer:** Espaço num artista conferido o marca como `↺ A DESFAZER`; o Enter pede um segundo Enter para confirmar. Desfazer reabre a última conferência (a data anterior e os lançamentos que estavam pendentes voltam) e **não apaga** a playlist no Qobuz.  

**Marcações salvas:** as marcações ainda não confirmadas ficam em `artist_review_selection.json`, ao lado do `library.db`, e são restauradas se o a-Shell for encerrado (por exemplo ao abrir o Safari); sair com `q` descarta.  
Sem terminal interativo (ou se a tela falhar) volta o prompt de texto com a mesma tabela numerada (`o 4` abre, `m 1,3` confirma).   

Abrir um perfil não marca o artista automaticamente. O primeiro scan estabelece a data-base e não coloca toda a discografia histórica na fila. Ao confirmar um artista, a data-base avança; só lançamentos com data igual ou posterior a ela entram como novidades. Quando a API não informa uma data utilizável, a primeira observação posterior à data-base é usada como fallback.  
O modo `--modo sorteio` prioriza artistas com novidades pendentes e mostra a tabela de lançamentos (`o` abre, `m` confirma e cria a playlist); `--sem-sincronizar` usa o catálogo já salvo. A revisão não favorita álbuns.

**Atalho do iOS para Notas.**  

No a-Shell, a ação **Execute Command** (opção **In App**) pode executar `python3 -m qobuz_dl --quiet artists --json`.  

A saída é um objeto JSON `{"artists":[...]}` com ID Qobuz estável, nome e URL de cada artista; esse modo consulta os favoritos, sem varrer discografias.  
No Atalhos, use a saída em **Get Dictionary from Input**, leia a chave `artists` e acrescente novos itens com a ação **Append Checklist Item** do Notas.  
Para impedir duplicatas entre execuções, mantenha um registro persistente dos IDs já adicionados (por exemplo, em um arquivo de texto no iCloud Drive/Atalhos); compare pelo ID, não pelo nome, pois nomes não são únicos.  
Um registro recém-criado não reconhece automaticamente artistas que já estavam em uma nota antiga sem IDs.

LINK DO ATALHO: [CLIQUE AQUI](https://www.icloud.com/shortcuts/74bb82306a9e443f9687e37aae598c54)  

_No IOS, é necessário ter o [A-Shell](https://apps.apple.com/br/app/a-shell/id1473805438) instalado_

**Sincronizar a Nota com o banco.**  

`qobuz-dl artists --sincronizar-checklist [ARQUIVO]` lê a checklist do Apple Notas (linhas `- [x] Nome - [QOBUZ_ID:123] - URL`; sem ARQUIVO usa `tracklist-artists.txt` ao lado do `config.ini`) e imprime um único JSON.  
Só mexe no banco local: **não faz login nem usa a rede** e não cria playlists.  
Por padrão só **marca**: `[x]` na Nota de um artista ainda não conferido o marca no banco, e quem você já conferiu na tela do a-Shell continua conferido mesmo se a Nota ainda estiver `[ ]` (vem em `kept_verified`, para você marcar na Nota).  

Use `--espelhar` para a Nota mandar (reabre quem está conferido no banco e desmarcado na Nota).  
A regra de "conferido" é a da tela: tem data de conferência **e** nenhum lançamento novo pendente.  
Artista marcado `[x]` na Nota que ganhou lançamento novo **não** é remarcado (apagaria as novidades sem você ver); ele vem em `needs_review`, com `pending_count`, para o Atalhos desmarcar na Nota. `missing` traz as linhas, prontas para colar na Nota, dos seguidos que ainda não estão nela.  
`--atualizar-seguidos` atualiza antes a lista de seguidos pela API (esta variante exige login). Como a marcação grava a data-base do momento da sincronização, sincronize logo depois de conferir.

**Primeira vez com uma biblioteca grande:** rode `sync-favorites` (sem download) e depois `scan`, para marcar o que você já tem antes de usar `--download-missing`.

**Como o scan decide.** Ordem: tag `QOBUZALBUMID` → UPC (`BARCODE`) → nome exato normalizado → fuzzy. Só marca sozinho quando o match é único e a profundidade de bits e o número de faixas batem; qualquer dúvida (2 candidatos, faixas faltando, pasta `[INCOMPLETE]`, fuzzy) vai para revisão manual. Álbuns multi-disco (`CD 01`, `CD 02`) contam como um só. Pastas com tag de ID que não estão nos favoritos são *adotadas* (`--no-adopt` desativa).

**Sentinela.** Cada álbum baixado recebe um `.streamrip.json` (mesmo formato do libsync) com serviço, ID e faixas. Serve para reconstruir o estado após perder o banco e detectar pastas movidas. Desative com `--no-sentinel` ou `write_sentinel = false` no `config.ini` (útil em montagens somente-leitura).

### 🛠️ Principais Variáveis de Formatação

Você pode personalizar profundamente seu `config.ini` ou usar as flags CLI `-ff` (Formato de Pasta) e `-tf` (Formato de Faixa) usando as variáveis abaixo. Você também pode usar o caractere `/` para criar subdiretórios aninhados automaticamente!

#### 📝 Tabela de Referência de Variáveis Completas

| Categoria / Variável | Descrição | Exemplo de Saída |
| :---| :--- | :--- |
| **Artistas & Compositores** | | |
| `{album_artist}` | O principal artista do álbum (lida com compilações graciosamente). | `Daft Punk` |
| `{artist}` / `{track_artist}` | O artista performático da faixa específica. | `Pharrell Williams` |
| `{album_composer}` | O compositor de todo o álbum/trabalho. | `Thomas Bangalter` |
| `{track_composer}` | O compositor da faixa específica. | `Guy-Manuel de Homem-Christo` |
| **Títulos e Versões** | | |
| `{album}` / `{album_title}` | Título do álbum (inclui versão como “Remasterizado”, se presente). | `Random Access Memories (Deluxe)` |
| `{album_title_base}` | Título do álbum base estritamente *sem* os detalhes da versão. | `Random Access Memories` |
| `{track_title}` / `{tracktitle}` | Título da faixa (inclui versão, se presente). | `Get Lucky (Radio Edit)` |
| `{track_title_base}` | Título da faixa base estritamente *sem* os detalhes da versão. | `Get Lucky` |
| `{version}` / `{album_version}` | Apenas a string da versão. | `Deluxe` |
| `{version_tag}` | Tag de versão inteligente (prepende um traço: ` - Deluxe`). Não deixa espaços de fuga se estiver vazio! | ` - Deluxe` |
| **Números e Datas** | | |
| `{track_number}` | O número da faixa (sempre acolchoado com zero inicial). | `08` |
| `{disc_number}` | O número de mídia do disco (acolchoado com zero inicial). | `01` |
| `{track_count}` | Número total de faixas no álbum. | `13` |
| `{disc_count}` | Número total de discos no álbum. | `1` |
| `{year}` | O ano de lançamento. | `2013` |
| `{release_date}` | A data de lançamento original completa. | `2013-05-17` |
| **Especificações Técnicas** | | |
| `{media_type}` | Tipo de produto bruto extraído da API (capitalizado). | `Album` |
| `{quality_tag}` | Tag inteligente combinando formato e profundidade de bits (fallback MP3 limpo). | `FLAC 24` |
| `{album_url}` | A URL oficial do Qobuz do lançamento. | `https://play.qobuz.com/...` |
| `{bit_depth}` | A profundidade do bit de áudio. | `24` |
| `{sampling_rate}` | A taxa de amostragem de áudio em kHz. | `88.2` |
| `{format}` | O formato de arquivo baixado. | `FLAC` |
| **Metadata & IDs** | | |
| `{release_type}` | Classificação do tipo de lançamento inteligente (`Álbum`, `EP`, `Single`). | `Album` |
| `{explicit}` / `{ExplicitFlag}` | Adiciona uma tag `[E]` se o aviso dos pais estiver ativo (vazio se estiver limpo). | `[E]` |
| `{album_genre}` | Gênero principal do lançamento. | `Electronic` |
| `{label}` | O nome da gravadora. | `Columbia` |
| `{copyright}` | String de direitos autorais. | `℗ 2013 Daft Life` |
| `{barcode}` / `{upc}` | O UPC/Código de Barras global da versão. | `888837168618` |
| `{isrc}` | O identificador ISRC exclusivo da faixa. | `USSM11302305` |
| `{album_id}` / `{track_id}` | IDs de banco de dados internos do Qobuz. | `123456789` |

#### 💡 Exemplos Práticos

**1. A Estratégia “Arquivo Audiófilo” (Pastas Aninhadas)**
Organiza por Gênero, depois Artista, depois Álbum com especificações técnicas completas:
* `folder_format = {album_genre}/{album_artist}/{album_artist} - {album_title}{version_tag} ({year}) [{bit_depth}B-{sampling_rate}kHz]`
* Saída: `Electronic/Daft Punk/Daft Punk - Random Access Memories - Deluxe (2013) [24B-88.2kHz]`

**2. A Estratégia “Biblioteca Limpa” (Tags Inteligentes de Versão e Conteúdo Explícito)**
Mantém simples, mas adiciona `[E]` somente se explícito e versões sem deixar hifens vazios:
* `folder_format = {album_artist} - {album_title_base}{version_tag} {ExplicitFlag}`
* Saída: `Eminem - The Eminem Show [E]`

**3. A Estratégia de Faixa “Arquivista”**
* `track_format = {track_number} - {track_title} [{isrc}]`
* Saída: `08 - Get Lucky [USSM11302305].flac`

## 🔧 Solução de Problemas: Ambientes Headless e Servidores
Se você estiver executando o `qobuz-dl` em um NAS, Docker ou em um sistema Linux headless (como WSL sem daemon de chaveiro de interface gráfica), você poderá encontrar erros `401 Unauthorized` após redefinir a configuração.

**A Solução:**
Ao executar `python -m qobuz_dl -r`, o assistente de configuração agora perguntará: 
`"Disable OS Keyring and save tokens in config.ini?"` (Desativar Keyring do SO e salvar tokens no config.ini?)
Selecione **`yes`** se estiver em um ambiente de servidor ou NAS. Isso ignora o chaveiro do sistema e persiste tokens sem criptografia no `config.ini`. A permissão `0600` restringe o acesso ao usuário atual, mas não criptografa o conteúdo; proteja e nunca compartilhe esse arquivo.

### 🩺 Verificação manual de integridade de áudio

A verificação de integridade por decodificação real pode ser ativada em cada download com `--verify-download` (ou `verify_after_download = true` no `config.ini`). O utilitário manual não é instalado pelo pacote nem incluído na imagem Docker; a partir de um checkout do código-fonte, rode:

O transporte direto de áudio também é finalizado de forma atômica: durante a rede o arquivo fica em `.part`, o tamanho e a resposta HTTP são conferidos e só então o nome final é promovido. Se a conexão cair, o mesmo download tenta continuar por `Range`; um arquivo parcial não aparece como faixa concluída.

```bash
python check_audio.py
```

É interativo: pergunta a pasta de música (sugerindo a do `config.ini` ou o diretório atual), varre as subpastas em busca de arquivos de áudio, e mostra um menu pra você escolher qual inspecionar. Reusa a mesma lógica de `qobuz_dl/utils.py` usada pelo downloader via FFmpeg.

## 🏆 Créditos
* **[vitiko98](https://github.com/vitiko98/qobuz-dl)**: Criador do projeto original.
* **[xwell](https://github.com/xwell/qobuz-dl)**: Pela grande reformulação do mecanismo de tags e integração com os “Goodies”.
* **[catap](https://github.com/catap)**: Pelo patch de download segmentado.
* **JosiahDanger**: Relatórios de bugs e sugestões de recursos.
* **Sorrow446 e DashLt**: O `qobuz-dl` é inspirado no projeto descontinuado Qo-DL-Reborn. Esta ferramenta utiliza o módulo principal de API `qopy`, originalmente escrito por eles.

## ⚠️ Isenção de Responsabilidade (Aviso Legal)
* Esta ferramenta foi desenvolvida para fins educacionais.
* O `qobuz-dl` não é afiliado ao Qobuz.
