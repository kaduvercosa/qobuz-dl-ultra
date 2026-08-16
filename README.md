# qobuz-dl Edição Ultra
[![Versão PyPI](https://img.shields.io/pypi/v/qobuz-dl-ultra.svg)](https://pypi.org/project/qobuz-dl-ultra/) [![Downloads PyPI](https://static.pepy.tech/personalized-badge/qobuz-dl-ultra?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/qobuz-dl-ultra) ![Docker Image CI](https://github.com/kaduvercosa/qobuz-dl-ultra/actions/workflows/docker.yml/badge.svg) [![Abrir no Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Sei969/qobuz-dl/blob/master/Qobuz_Ultimate_Colab.ipynb)

Pesquise, explore e baixe músicas Lossless e Hi-Res do [Qobuz](https://www.qobuz.com/).

**Este é um fork aprimorado e repleto de recursos do projeto original qobuz-dl, projetado para a melhor experiência audiófila. Ele inclui um mecanismo de download resiliente com filtragem inteligente anti-spam, personalização profunda para manter sua biblioteca perfeitamente organizada e suporte nativo abrangente para metadados de música clássica.**

## ✨ Funcionalidades

### 🎧 Mecanismo Audiófilo e de Metadados
* **Otimizado para Roon e DAP:** Metadados, capas e letras são meticulosamente formatados para garantir integração perfeita e imediata com servidores Roon e Tocadores de Áudio Digital (DAPs).
* **Tagging Direto de URL do Álbum:** Gera e incorpora automaticamente um link clicável direto `QOBUZ ALBUM URL` nos metadados da faixa (Vorbis Comments para FLAC, quadro `TXXX` para MP3). Isso permite o acesso com um clique à página original do álbum no Qobuz diretamente de editores de tags como o Mp3tag ou reprodutores audiófilos compatíveis. Você pode opcionalmente desativar essa tag não padrão usando a flag `—no-album-url-tag` (ou `no_album_url_tag = true` no arquivo de configuração).
* **Letras Sincronizadas Prontas para o Roon:** O mecanismo formata e incorpora inteligentemente dados `.lrc` com marcação de tempo diretamente nos arquivos de áudio (Vorbis Comments `[LYRICS]`), garantindo que o Roon exiba nativamente letras roláveis em estilo karaokê na visualização “Tocando Agora” (Now Playing). Se você preferir uma estrutura de pastas minimalista e organizada, pode desativar totalmente a geração de arquivos `.lrc` externos via CLI (`—no-lrc-files`). Por outro lado, se preferir arquivos externos sem inflar os metadados do áudio, use a nova flag `—no-embed-lyrics` (ou defina `embed_lyrics = false` na configuração).
* **Controle Amplo de Tags:** O mecanismo de tags reformulado suporta metadados altamente detalhados de música clássica. Quase todas as tags podem ser ativadas/desativadas via argumentos de linha de comando (CLI).
* **Tradução Inteligente de Gêneros:** Traduz automaticamente gêneros persistentes em francês (ex.: *Électronique*, *Bande Originale*) para o inglês padrão, garantindo que sua biblioteca permaneça consistente e pesquisável.
* **Tagging Nativo Multi-Artista / Multi-Valor e Análise Profunda de Intérpretes:** Detecta e separa automaticamente artistas principais, participações especiais e extrai *todos* os compositores/letristas de strings de metadados complexas do Qobuz. Com a nova flag CLI `—multi-tags`, o mecanismo divide inteligentemente metadados separados por vírgula em tags multi-valor discretas para FLAC (Vorbis Comments) e MP3 (ID3v2.4), garantindo interpretação impecável da biblioteca por players avançados como Roon, MusicBee ou Plexamp.
* **Suporte Nativo a ReplayGain:** Extrai e incorpora automaticamente as tags `REPLAYGAIN_TRACK_GAIN` e `REPLAYGAIN_TRACK_PEAK` diretamente dos dados ocultos da API do Qobuz. Isso garante nivelamento de volume perfeito e não destrutivo de fábrica para tocadores de áudio digital (DAPs) de alta fidelidade e servidores audiófilos como o Roon.
* **Mecanismo Automático de Letras e Tagger Retroativo:** Busca e injeta letras sincronizadas (`.lrc`) e não sincronizadas usando o LRCLIB (com fallback para a API do Genius). Inclui o comando dedicado `lyrics` para escanear retroativamente e injetar letras ausentes em sua biblioteca local existente sem precisar baixar novamente o áudio.
* **Encartes Digitais Aprimorados (Digital Booklets):** Compila automaticamente um arquivo `.txt` formatado com lista de faixas completa, duração, créditos detalhados, metadados e resenhas. Ao concluir, o mecanismo varre a pasta, remove as marcações de tempo dos arquivos `.lrc` e anexa as letras em texto puro de todo o álbum diretamente no encarte. “Goodies” oficiais em PDF também são baixados junto. **Agora você pode usar a flag `—booklet-only` para baixar exclusivamente esses arquivos de metadados, capas e PDFs, ignorando graciosamente todas as faixas de áudio pesadas.**
* **Correção do Campo de Compositor:** Extrai meticulosamente cada compositor individual da string completa de intérpretes, acabando com o problema de metadados de compositores truncados ou “aleatórios”.
* **Formatação Inteligente de Datas:** Padroniza as datas de lançamento em entradas únicas e limpas, evitando conflitos de tags duplicadas de ano/data em softwares de reprodução.
* **Modo Bit-Perfect & Purista:** Desative completamente as tags de volume ReplayGain e Peak usando a flag CLI `—no-replaygain-tag` (ou `no_replaygain_tag = true` na configuração). Isso garante que seus arquivos de áudio permaneçam estritamente bit-perfect e intocados por quaisquer instruções de nivelamento de volume via software, ideal para DACs de alta fidelidade e DAPs dedicados.

### 🚀 Mecanismo de Download Resiliente
* **Fila À Prova de Falhas:** Tratamento avançado de exceções no nível da faixa. Se uma única faixa estiver bloqueada geograficamente ou ausente nos servidores (erro 404), o mecanismo a pula normalmente e continua baixando o restante do seu álbum ou playlist sem travar.
* **Recuperação e Sincronização de Banco de Dados:** Inclui um mecanismo especializado `—sync-db` para restaurar entradas ausentes em seu banco de dados local escaneando suas pastas de música existentes.
* **Sincronização Bidirecional de Playlists (`sync-playlist`):** Um poderoso mecanismo de espelhamento para playlists dinâmicas. Mantenha suas pastas locais perfeitamente sincronizadas com as alterações online (baixando novas faixas e excluindo de forma limpa as que foram removidas). **A v2.0.1 introduz a Lógica Inteligente de Pastas (Smart Folder Logic):** ao usar `-d .` ou caminhos genéricos, ele cria automaticamente uma subpasta com o nome da playlist, evitando a exclusão acidental de arquivos no seu diretório raiz.
* **Tabela Profissional de Faixas Ausentes:** Se o mecanismo de sincronização detectar faixas na sua playlist online que estejam ausentes no seu disco local, ele gera uma tabela ASCII limpa e colorida com Título, Artista e ID para fácil acompanhamento.
* **Busca Reversa Inteligente (Reverse Lookup):** Identifica automaticamente arquivos antigos lendo suas tags **ISRC** ou **UPC** e consultando a API do Qobuz para restaurar os IDs corretos no banco de dados.
* **Validação Inteligente Prévia de Configuração:** Introduzido na v2.0.3, um sistema de validação inteligente verifica as strings de formatação do seu `config.ini` antes de iniciar qualquer download. Se detectar uma variável não reconhecida, o mecanismo aborta o processo com segurança e usa `difflib` para sugerir inteligentemente a variável correta, evitando exceções `KeyError` silenciosas.
* **Download Segmentado e Remuxagem:** Contorna a limitação de velocidade (throttling) da CDN da Akamai com um mecanismo de download segmentado de alta velocidade e remuxagem automática via FFmpeg.
* **Download Multithread:** Downloads simultâneos de faixas para obtenção ultrarrápida de álbuns.
* **Interface Limpa para Multithreading:** Alterna de forma inteligente para um sistema de registros estático e limpo exibindo tamanhos precisos de arquivo (MB) durante downloads concorrentes. Isso evita falhas visuais no terminal e conflitos com o mecanismo de letras, enquanto preserva as barras de progresso animadas clássicas para downloads sequenciais (`—delay`).
* **Recuperação de Terminal (Correção do Raw Mode):** Corrigido um bug crítico de interface onde a interrupção do prompt de busca interativo (modo `fun`) com `CTRL+C` deixava o terminal do sistema operacional em um estado quebrado. O mecanismo agora aciona com segurança uma saída graciosa do sistema, restaurando a disciplina de linha padrão do terminal.
* **Fallback Inteligente de Qualidade:** Reduz automaticamente para a próxima melhor qualidade disponível caso o nível solicitado seja restrito pelo servidor, garantindo que sua fila de download nunca trave.
* **Bypass de Autenticação:** Faça login com segurança usando o **Token de Autenticação** (Auth Token) do seu navegador caso a autenticação padrão por senha esteja bloqueada. Suporta perfeitamente contas Free e Studio.
* **Armazenamento Seguro de Credenciais (Keyring do SO):** Diga adeus às senhas em texto puro. Os tokens de autenticação (Qobuz e Genius) são criptografados com segurança e armazenados nativamente no gerenciador de credenciais do seu sistema operacional (Windows Credential Manager, macOS Keychain ou Linux Secret Service). **Recurso da Edição Ultimate:** Suporte nativo para ambientes headless (NAS/Docker/WSL). Se o sistema não tiver um daemon de chaveiro seguro, o mecanismo fornece um recurso robusto de “Autocorreção” (Self-Healing): ele detecta automaticamente o problema e oferece a flag `disable_keyring`, permitindo o armazenamento seguro no `config.ini` e evitando erros persistentes ‘401 Unauthorized’.
* **Camuflagem Anti-Banimento (Stealth Spoofing):** WAFs (Web Application Firewalls) modernas bloqueiam requisições de API originadas de scripts headless. Este mecanismo conta com camuflagem criptográfica completa, injetando Client Hints exatos do Windows/Chrome (`Sec-Ch-Ua`, `Sec-Fetch-Site`) para tornar sua sessão indistinguível de um usuário legítimo navegando no Qobuz Web Player, reduzindo significativamente erros 403 e prevenindo banimentos de conta.
* **Playlists Sem Limites:** Supera as restrições da API do Qobuz paginando dinamicamente as requisições em blocos, permitindo que você enfileire e baixe playlists massivas sem o gargalo padrão de 50 faixas.
* **Retomada Inteligente (Sem Sobrescritas):** Detecta inteligentemente arquivos existentes no seu disco local e os ignora automaticamente. Se o download de uma discografia massiva for interrompido, ele é retomado instantaneamente sem desperdiçar tempo ou largura de banda baixando faixas existentes.
* **Mecanismo de Blacklist Anti-Spam:** Filtre automaticamente lançamentos indesejados (“lixo”, como versões em Karaokê, covers instrumentais, álbuns de tributo) ao baixar discografias completas de artistas ou catálogos de gravadoras. Você pode passar um arquivo `.txt` contendo suas palavras-chave personalizadas (ex.: `Karaoke`, `(Live)`, `Original Soundtrack`) pela flag CLI `-b` ou configurá-lo permanentemente no seu `config.ini`. O mecanismo combina dinamicamente o título principal e as tags de versão, garantindo filtragem perfeita antes que um único byte de áudio seja baixado.
* **Download em Lote com Estado (Memória em Arquivo de Texto):** Ao baixar filas massivas a partir de um arquivo `.txt`, o mecanismo atua como um banco de dados vivo. Ele valida URLs automaticamente e anexa uma tag `[DONE]` ao lado dos links concluídos diretamente dentro do arquivo de texto. Se sua conexão cair ou você abortar o processo, basta rodar novamente o comando: o mecanismo pulará instantaneamente os links concluídos e continuará a fila exatamente de onde parou.
* **Geração Impecável de `.m3u`:** Gera automaticamente arquivos de playlist com caminhos relativos corretos. **A v2.0.1 apresenta um algoritmo robusto de correspondência em 4 etapas** (ID -> ISRC -> Título -> Nome do arquivo) que garante que o arquivo `.m3u` espelhe com precisão a ordem da API, mesmo quando as faixas não possuem prefixos numéricos em seus nomes.
* **Mecanismo de Correspondência O(1) Ultrarrápido:** O gerador de playlists agora utiliza indexação de dicionário de alto desempenho. Ele identifica arquivos locais instantaneamente, reduzindo o tempo de processamento de playlists grandes de segundos para milissegundos. (Agradecimentos a marrobHD)
* **Arquivos Temporários Compatíveis com NAS e macOS:** Arquivos temporários de download agora usam um prefixo padrão `~tmp_` em vez de um ponto inicial. Isso impede que sistemas baseados em Unix (macOS, Synology SMB/Samba) apliquem permanentemente atributos de sistema “Oculto” aos seus arquivos de áudio, eliminando a necessidade de comandos de limpeza no terminal.

### 📁 Formatação Avançada e Armazenamento

O Qobuz-DL Ultimate permite profunda personalização da estrutura da sua biblioteca usando variáveis.

* **Suporte a Playlists Reais (Nativo):** Lida perfeitamente com playlists do Qobuz e Last.fm com uma lógica especializada projetada para organização de biblioteca (Resolve a issue #257).
* **Estrutura de Pasta Plana (Flat):** Baixa automaticamente todas as faixas em um único diretório nomeado com o título da playlist, evitando a criação de dezenas de subpastas de álbuns dispersas.
* **Nomenclatura Independente de Posição:** Arquivos de áudio são salvos de forma limpa (ex.: `Artista - Título.flac`) sem prefixos numéricos fixos. Essa abordagem padrão da indústria garante que, se a ordem da playlist mudar online, seus arquivos locais sejam reconhecidos instantaneamente, evitando downloads duplicados em massa.
* **`.m3u` Inteligente Baseado na API:** A ordem de reprodução é garantida por um arquivo `.m3u` gerado dinamicamente que espelha com perfeição a sequência exata ditada pelos servidores do Qobuz, independentemente dos nomes físicos dos arquivos.
* **Gerenciamento Inteligente de Capas:** Elimina o bug de “Conflito de Capas”. O mecanismo gerencia dinamicamente as artes incorporadas, garantindo que cada faixa receba sua capa exclusiva correta sem deixar arquivos `cover.jpg` duplicados na pasta.
* **Substituição de Modo de Álbum (`—playlist-as-albums`):** *Novo recurso.* Se você usa playlists para buscar músicas específicas, essa flag ignora completamente a lógica de Pasta Plana. O mecanismo vai “explodir” a playlist, direcionando cada faixa para sua respectiva pasta de álbum original usando o seu `folder_format` padrão, mantendo os números de faixa originais e baixando a capa específica de cada álbum.
* **Variáveis Poderosas:** `folder_format` e `track_format` agora suportam dezenas de novas variáveis (ex.: `{isrc}`, `{barcode}`, `{label}`, `{track_composer}`).
* **Tipo de Lançamento (`{release_type}`):** Identifica automaticamente a categoria de publicação a partir das APIs do Qobuz (ex.: `Album`, `EP`, `Single`), permitindo que você encaminhe dinamicamente downloads para subdiretórios ou use como prefixo de nomenclatura sem impor uma estrutura fixa.
  * *Exemplo de Pasta (Subdiretório):* `folder_format = {release_type}/{album_artist} - {album_title}` ➔ `Album/Daft Punk - Discovery`
  * *Exemplo de Pasta (Prefixo):* `folder_format = {release_type} - {album_artist} - {album_title}` ➔ `Single - Gorillaz - Silent Running`
* **Tag Explícita (`{explicit}` ou `{ExplicitFlag}`):** Adiciona automaticamente uma tag `[E]` se a faixa ou álbum tiver aviso parental no Qobuz. Se o conteúdo for limpo, a variável permanece vazia sem deixar espaços finais indesejados. **Você pode aplicar isso permanentemente adicionando as variáveis ao seu arquivo `config.ini`, ou temporariamente via CLI usando as flags `-ff` e `-tf`.**
  * *Exemplo de Pasta:* `folder_format = {artist} - {album} {ExplicitFlag}` ➔ `Eminem - The Eminem Show [E]`
  * *Exemplo de Faixa:* `track_format = {track_number} - {track_title} {ExplicitFlag}` ➔ `02 - Without Me [E].flac`
* **Tag de Versão do Álbum (`{version_tag}`):** Adiciona automaticamente a versão do álbum (ex.: Live, Remastered, Deluxe Edition) ao nome da pasta ou faixa. Se o lançamento for uma edição padrão, a variável permanece completamente vazia, evitando espaços ou hifens indesejados.
  * *Exemplo de Pasta (Padrão):* `folder_format = {album_artist} - {album_title}{version_tag}` ➔ `The Sunset Violent`
  * *Exemplo de Pasta (Edição Especial):* `folder_format = {album_artist} - {album_title}{version_tag}` ➔ `The Sunset Violent - Live in Heidelberg`
* **Roteamento Multi-Disco:** Armazene lançamentos com múltiplos discos em um único diretório ou divida-os usando prefixos personalizáveis (ex.: `CD 01`).
* **Geração Universal de Playlists:** Arquivos `.m3u` são rigorosamente codificados em UTF-8, garantindo 100% de estabilidade mesmo com caracteres Unicode complexos ou japoneses (Resolve a issue #304).
* **Substituição de Caracteres Legados (`legacy_charmap`):** Por padrão, a Edição Ultimate usa caracteres Unicode de largura total elegantes (ex.: `／`) para contornar com segurança as restrições de nomes de arquivos do SO sem perder a estética do título original. No entanto, puristas podem ativar a opção `legacy_charmap = true` no seu `config.ini` para forçar substituições padrão em ASCII (ex.: substituir `/` por `-` ou remover `?`), restaurando a convenção de nomenclatura clássica do qobuz-dl original.

### ❤️ Sincronização Nativa de Favoritos e Menu Interativo
Conecte perfeitamente seus hábitos de escuta móvel com sua biblioteca local offline. Em vez de copiar URLs manualmente, inicie o Modo Interativo (`fun`) para acessar sua conta pessoal do Qobuz com segurança e navegar pelos seus **Álbuns, Faixas, Artistas e Playlists Favoritos** diretamente do terminal.
* **Fluxo de Trabalho Sem Digitação:** Acesse sua biblioteca privada com um único clique sem nunca sair do terminal.
* **Download em Lote Massivo:** Use a `Barra de Espaço` para selecionar múltiplos lançamentos favoritos a partir da interface limpa e minimalista e enfileirar todos para download em segundos.
* **Filtro Inteligente de Lançamentos (Mecanismo Heurístico):** Ao buscar a discografia de um artista, o mecanismo executa um algoritmo heurístico local ultrarrápido para categorizar os lançamentos (Álbuns, EPs, Singles, Ao Vivo). Ele apresenta instantaneamente uma interface de caixas de seleção, permitindo filtrar singles ou compilações indesejados antes mesmo do início do download, economizando tempo e armazenamento.

### 🌉 Integração Inteligente com Last.fm e Modo Interativo
Conecte seu mundo do Last.fm ao Qobuz perfeitamente. Baixe suas playlists personalizadas e “Faixas Favoritas” (Loved Tracks) com facilidade. 
Para evitar o download de músicas incorretas, este fork utiliza um **Algoritmo Matemático de Correspondência Difusa (Fuzzy Matching)**:
* **Aceitação Automática (> 75%):** Correspondências perfeitas são enfileiradas automaticamente.
* **Pulo Automático (< 60%):** Faixas completamente erradas são ignoradas automaticamente.
* **Seleção Interativa (60% - 74%):** Para correspondências limítrofes, o mecanismo pausa e ativa um prompt interativo permitindo que você aprove ou rejeite manualmente a faixa (`[y/n]`).

### 📡 Radar RSS do MusicButler (Sincronização Automatizada de Favoritos)
Nunca perca um novo lançamento dos seus artistas monitorados. O novo comando `radar` se integra perfeitamente ao seu feed RSS privado do **MusicButler** para automatizar seu fluxo de descobertas.
* **Análise Inteligente de Feed:** Busca e analisa automaticamente seu feed privado RSS/Atom para encontrar os lançamentos mais recentes dos artistas que você segue.
* **Correspondência Difusa com o Qobuz:** Consulta o banco de dados do Qobuz para encontrar as correspondências exatas em alta resolução para os seus novos lançamentos diários.
* **Interface Interativa com Caixas de Seleção:** Apresenta um menu de terminal interativo limpo onde você pode selecionar múltiplos lançamentos novos (`Barra de Espaço`) e injetá-los instantaneamente nos seus Favoritos do Qobuz (`Enter`), prontos para serem baixados posteriormente pelo modo `fun`.

### 🛡️ Gerenciamento de Pastas À Prova de Falhas e Retomada Inteligente
Diga adeus a bibliotecas desorganizadas e downloads corrompidos. O baixador agora conta com um sistema dinâmico de 3 estágios de estado de pasta para manter sua biblioteca perfeitamente organizada:
* **`[IN PROGRESS]`**: Pastas são marcadas enquanto o download está em andamento.
* **`[INCOMPLETE]`**: Se você abortar o processo (tratamento suave de `CTRL+C`) ou se algumas faixas forem puladas (ex.: bloqueio regional ou indisponibilidade), a pasta é marcada com segurança como incompleta. 
* **Estado Limpo (Clean State)**: Somente quando um álbum for baixado com **100% de sucesso** a pasta será renomeada para seu estado final limpo (ex.: `Artista - Álbum`).

*Nota: O mecanismo é inteligente o suficiente para retomar downloads diretamente em pastas `[INCOMPLETE]` ou `[IN PROGRESS]` na sua próxima execução!*

## 📥 Instalação e Configuração

> ⚠️ **Requisito:** Você precisa de uma **assinatura ativa** do Qobuz.

### Opção A: 📦 Pacote PyPI (Recomendado para todas as plataformas)
A maneira mais fácil e oficial de instalar a Edição Ultimate. Abra seu terminal e execute:
```bash
pip install qobuz-dl-ultimate
```
*Após a instalação, você pode iniciar o programa de qualquer pasta no seu computador digitando simplesmente `qobuz-dl` ou `qdl`.*

### Opção B: Binários Pré-compilados (Windows x64)
A maneira mais fácil de executar o programa no Windows sem precisar instalar o Python.
👉 **[Baixe o ZIP mais recente aqui](https://github.com/kaduvercosa/qobuz-dl-ultra/releases/latest)**
* **Portátil:** Nenhuma instalação necessária.
* **Importante:** Apenas extraia o `.zip` e certifique-se de que `ffmpeg.exe` e `qobuz-dl-ultimate.exe` estejam na mesma pasta.

### Opção C: Código-fonte Python (Avançado)
Clone este repositório e instale as dependências necessárias:
```bash
git clone https://github.com/kaduvercosa/qobuz-dl-ultra.git
cd qobuz-dl
pip3 install -r requirements.txt
```
*Execute o programa usando:* `python -m qobuz_dl`

### Opção D: 🐳 Uso com Docker (NAS e Servidores Caseiros)
A Edição Ultimate é totalmente conteinerizada e inclui todas as dependências (Python, FFmpeg). Este é o método de instalação recomendado para Synology, QNAP, Unraid e servidores headless.
```bash
# Baixar a imagem oficial mais recente
docker pull ghcr.io/kaduvercosa/qobuz-dl:latest

# Exemplo: Executar um download e mapeá-lo para a pasta de músicas do seu NAS
docker run -it —rm \
  -v /caminho/para/suas/musicas/no/nas:/app/QobuzDownloads \
  ghcr.io/kaduvercosa/qobuz-dl:latest dl “https://play.qobuz.com/album/...”
```

### Opção E: ☁️ Google Colab (Nuvem e Google Drive)
A maneira mais rápida de baixar diretamente para o seu Google Drive em velocidades de Gigabit, contornando limitações da rede local. Nenhuma instalação necessária.

[![Abrir no Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Sei969/qobuz-dl/blob/master/Qobuz_Ultimate_Colab.ipynb)

* **Zero Configuração:** Executa inteiramente no seu navegador (funciona perfeitamente em smartphones e tablets também).
* **Uso:** Clique no badge acima, execute as células de configuração para montar seu Google Drive, cole seu Token de Autenticação do Qobuz e comece a baixar diretamente para a nuvem.

### ⚙️ Configuração e Caminhos Personalizados
Se você deseja definir uma pasta de download personalizada, pode editar seu arquivo `config.ini` e usar a chave `directory`. Caminhos absolutos e o operador `~` (para macOS/Linux) são totalmente suportados!

> **💡 Dica para usuários que estão atualizando:** Para acessar as opções de configuração mais recentes (como `embed_lyrics` e `multi_value_tags`), basta executar `qobuz-dl -r` para redefinir e gerar um novo arquivo `config.ini`, ou adicioná-las manualmente na seção `[qobuz]`.

```ini
[qobuz]
directory = ~/Music/Qobuz_Lossless

# Defina como ‘true’ se estiver executando em um servidor headless (NAS/Docker/WSL) 
# para salvar os tokens no config.ini em vez do Keyring do SO.
disable_keyring = false

# Defina como ‘true’ para restaurar substituições clássicas de caracteres ASCII
legacy_charmap = false

# Defina como ‘true’ para desativar a geração de arquivos .lrc externos
no_lrc_files = true

# Defina como ‘true’ para restaurar substituições clássicas de caracteres ASCII (ex.: substituir ‘/‘ por ‘-‘)
legacy_charmap = false

# Defina como ‘true’ para desativar a geração de arquivos .lrc externos (as letras serão incorporadas apenas nas tags FLAC/MP3)
no_lrc_files = true

# Defina como ‘true’ para desativar estritamente tags de volume ReplayGain para reprodução bit-perfect em hardware
no_replaygain_tag = true

# Defina como ‘true’ para desativar a gravação da tag não padrão “QOBUZ ALBUM URL” nos arquivos
no_album_url_tag = true

# Defina como ‘true’ para desativar a extração de metadados de música clássica
no_conductor_tag = true
no_ensemble_tag = true
no_work_tag = true
```
*(Nota: Se você estiver atualizando de uma versão anterior, a chave legada `default_folder` ainda é totalmente suportada para compatibilidade retroativa.)*

> **🔒 Nota de Segurança:** Seu `auth_token` e `genius_token` não estarão mais visíveis no seu `config.ini`. A Edição Ultimate os migra e criptografa automaticamente no gerenciador seguro de credenciais do seu sistema operacional (Keyring) para proteger suas contas.

### 🔑 Como obter seu Auth Token
Como o Qobuz bloqueou logins diretos por senha para aplicativos de terceiros, você precisa fornecer o Token de Autenticação do seu navegador durante a configuração inicial. Veja como encontrá-lo facilmente:
1. Abra o [Qobuz Web Player](https://play.qobuz.com) no seu navegador e faça login.
2. Pressione `F12` para abrir as Ferramentas de Desenvolvedor (Developer Tools).
3. Vá para a aba **Application** (Chrome/Edge) ou **Storage** / **Armazenamento** (Firefox).
4. Na barra lateral esquerda, expanda **Local Storage** (Armazenamento Local) e clique em `https://play.qobuz.com`.
5. Na lista de chaves, procure por **`localuser`**.
6. Na parte inferior do painel (ou expandindo o valor JSON), localize a string **`token`**.
7. Abra o terminal e force o assistente de login executando `qobuz-dl -r` (ou `—reset`). Quando o prompt aparecer, selecione o método Auth Token e cole sua sequência alfanumérica!

## 💻 Uso e Exemplos Rápidos

```text
[Comandos Globais e Gerenciamento de Banco de Dados]
usage: python -m qobuz_dl [-h] [-r] [-p] [—sync-db [PATH]] [-sc] {interactive,i,fun,dl,lucky,lyrics,radar,sync-playlist,sp,stats} ...

[Uso de Download]
usage: python -m qobuz_dl dl [-h] [-d PATH] [-q int] [—albums-only] [—no-m3u] [—no-fallback] [—no-db] 
                             [-ff PATTERN] [-tf PATTERN] [-s] [-e] [—no-cover]
                             [-b PATH]
                             [—embedded-art-size {50,100,150,300,600,max,org}] 
                             [—saved-art-size {50,100,150,300,600,max,org}] 
                             [—multiple-disc-prefix PREFIX] [—multiple-disc-one-dir] 
                             [—no-lyrics] [—no-lrc-files] [—native-lang] [—no-credits] [—with-credits] [—booklet-only] [—delay SECONDS] [—playlist-as-albums]
                             [—no-album-artist-tag] [—no-track-composer-tag] ... 
                             SOURCE [SOURCE ...]
```

**Modo Radar MusicButler:**
*(Dica: Execute uma vez para salvar seu link RSS e depois execute diariamente para capturar novos lançamentos e adicioná-los com segurança aos seus favoritos do Qobuz!)*
```bash
python -m qobuz_dl radar
```

**Sincronização Bidirecional de Playlist:**
*(Dica: Adicione `-y` para ignorar os avisos de confirmação. A flag `-d` opera com segurança, criando automaticamente uma subpasta para a playlist).*
```bash
python -m qobuz_dl sp “URL” -d “C:\Caminho\Para\Pasta\Local\Da\Playlist”
```
                            
**Download Básico de Álbum/Playlist:**
```bash
python -m qobuz_dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb
```

**Explodir Playlists em Álbuns:**
Por padrão, as playlists são baixadas em uma única pasta plana. Use esta flag se você usa playlists como uma “ferramenta de descoberta” e deseja que o mecanismo encaminhe dinamicamente cada faixa para sua respectiva pasta de álbum original, com sua arte de capa específica e metadados originais da faixa.
```bash
python -m qobuz_dl dl “URL_DA_PLAYLIST” —playlist-as-albums
```

**Download em Massa / Lote (Retomada Inteligente):**
Tem uma lista enorme de lançamentos para baixar? Crie um arquivo de texto comum (ex.: `list.txt`), cole suas URLs do Qobuz **e do Last.fm** nele (uma por linha) e passe-o para o mecanismo. O analisador inteligente baixará automaticamente seus links do Qobuz e roteará perfeitamente as playlists do Last.fm pelo mecanismo de Fuzzy Matching para processar toda a sua fila de uma só vez!
*Recurso da Edição Ultimate:* O arquivo de texto atua como um banco de dados vivo. Assim que um lançamento ou playlist completa é baixado com sucesso, o mecanismo anexa uma tag `[DONE]` ao lado da URL no arquivo. Se sua conexão cair ou você interromper o processo (`CTRL+C`), basta executar exatamente o mesmo comando novamente e o mecanismo pulará instantaneamente os links concluídos, retomando perfeitamente de onde parou.
```bash
python -m qobuz_dl dl list.txt
```

**Blacklist de Discografia e Modo Anti-Spam:**
Está baixando a discografia completa de um artista, mas quer evitar gastar espaço com versões de Karaokê, Tributo ou Instrumentais? Crie um arquivo de texto (ex.: `blacklist.txt`) contendo as palavras-chave indesejadas (uma por linha) e passe-o para o mecanismo. Ele inspecionará automaticamente cada lançamento e pulará o conteúdo indesejado!
*(Dica: Você pode definir `blacklist = blacklist.txt` no seu `config.ini` para tornar isso automático em cada download).*
```bash
python -m qobuz_dl dl https://play.qobuz.com/artist/123456 -b blacklist.txt
```

**Modo Anti-Ban Supremo (Camuflagem + Delay):**
Embora o mecanismo mascare nativamente sua pegada digital (Stealth Spoofing) para simular um navegador Chrome real, baixar 100 faixas em 10 segundos ainda é fisicamente impossível para um ser humano e pode acionar banimentos baseados em volume. Use este comando para grandes discografias para desativar o multithreading e adicionar um intervalo forçado entre as faixas, garantindo a máxima segurança para sua conta.
```bash
python -m qobuz_dl dl <URL> —delay 1
```

**Forçar Encartes e Créditos (Substituição de Configuração):**
Se você definiu `no_credits = true` no seu `config.ini` para manter suas pastas limpas, pode substituir temporariamente esse comportamento para forçar a geração do Encarte Digital e do Tracklist.txt para uma obra-prima específica.
```bash
python -m qobuz_dl dl <URL> —with-credits
```

**Análise de Múltiplas Tags e Intérpretes:**
Use a flag `—multi-tags` para garantir que faixas complexas com múltiplos artistas e compositores sejam divididas em campos limpos e individuais nas tags de áudio.
```bash
python -m qobuz_dl dl “URL” —multi-tags
```

**Modo Apenas Metadados e Encarte:**
Quer completar os metadados da sua biblioteca sem baixar gigabytes de áudio? Este comando busca apenas a arte da capa, gera o encarte com lista de faixas/créditos, baixa os Goodies em PDF oficiais e ignora com segurança todas as faixas de áudio.
```bash
python -m qobuz_dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb —booklet-only
```

**Modo de Pasta Minimalista (Sem arquivos .lrc externos):**
Baixa o álbum e injeta as letras sincronizadas exclusivamente nos metadados do FLAC/MP3, mantendo suas pastas completamente limpas de arquivos de texto externos.
```bash
python -m qobuz_dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb —no-lrc-files
```

**Roteamento Avançado de Discografia:**
Salve múltiplos discos de um lançamento em uma única pasta em vez de dividi-los.
```bash
python -m qobuz_dl dl https://play.qobuz.com/artist/2038380 —multiple-disc-one-dir
```

**Modo Interativo Last.fm (Modo Fun):**
*(Dica: No modo interativo, use `Espaço` para selecionar múltiplos álbuns para baixar de uma vez!)*
```bash
python -m qobuz_dl fun -l 10
```
**Modo Audiófilo Purista (Sem ReplayGain):**
Baixe uma faixa mantendo o arquivo estritamente bit-perfect, sem gravar quaisquer tags de nivelamento de volume (útil para DSPs e DAPs de hardware).
```bash
python -m qobuz_dl dl “URL” —no-replaygain-tag
```

### 🗄️ Gerenciamento de Banco de Dados e Biblioteca
A Edição Ultimate inclui poderosos gerenciadores de biblioteca local para acompanhar seus downloads, evitar duplicatas e corrigir seus metadados retroativamente.

* **Sincronização Inteligente de Biblioteca (`—sync-db`):**
  Já possui uma biblioteca local de FLACs baixados? Não precisa começar do zero. Execute este comando para realizar uma *Busca Reversa* no seu diretório de downloads. O mecanismo escaneará seus arquivos existentes e os injetará automaticamente no banco de dados local para evitar downloads duplicados no futuro.
  ```bash
  python -m qobuz_dl —sync-db
  ```
  *(Nota: Você também pode especificar um caminho personalizado para escanear, ex.: `—sync-db “/caminho/para/suas/musicas”`)*

* **Sincronização Dinâmica de Playlists (`sync-playlist` / `sp`):**
  Playlists são entidades vivas. Em vez de baixar novamente uma playlist inteira toda vez que o autor adiciona uma nova música, aponte este comando para a sua pasta existente. Ele escaneará as tags locais, consultará a API do Qobuz e calculará o delta exato: baixando apenas as faixas ausentes, excluindo de forma limpa as removidas (junto com seus respectivos arquivos `.lrc`) e regenerando a ordem no `.m3u`.
  ```bash
  python -m qobuz_dl sp “URL_DA_PLAYLIST” -d “/caminho/para/sua/pasta/local”
  ```

* **Tagger Retroativo de Letras (`lyrics`):**
  Tem uma biblioteca de músicas local existente que não possui letras sincronizadas? O novo comando `lyrics` funciona como um mecanismo autônomo de metadados. Ele varre recursivamente qualquer diretório local, detecta arquivos FLAC/MP3 sem letras e as injeta de forma inteligente nos arquivos de áudio usando o LRCLIB (e a API do Genius) sem baixar novamente nenhuma música.
  ```bash
  python -m qobuz_dl lyrics “/caminho/para/sua/pasta/local/de/musica”
  ```

* **Limpar Banco de Dados (`-p`, `—purge`):**
  Se você precisar recomeçar do zero, limpar seu histórico de downloads ou corrigir um estado corrompido, pode apagar instantaneamente o banco de dados local com um único comando.
  ```bash
  python -m qobuz_dl —purge
  ```

* **Estatísticas do Usuário (`stats`):**
  Curioso sobre seus hábitos de download? Este comando consulta instantaneamente seu banco de dados SQLite local para exibir estatísticas dos seus downloads, incluindo o número total de artistas únicos baixados e uma lista alfabética completa da sua biblioteca.
  ```bash
  python -m qobuz_dl stats
  ```

### 🛠️ Principais Variáveis de Formatação

Você pode personalizar profundamente seu `config.ini` ou usar as flags CLI `-ff` (Formato de Pasta) e `-tf` (Formato de Faixa) usando as variáveis abaixo. Você também pode usar o caractere `/` para criar subdiretórios aninhados automaticamente!

#### 📝 Complete Variables Reference Table

| Variable | Description | Example Output |
| :--- | :--- | :--- |
| **Artists & Composers** | | |
| `{album_artist}` | The main artist of the album (handles compilations gracefully). | `Daft Punk` |
| `{artist}` / `{track_artist}` | The performing artist of the specific track. | `Pharrell Williams` |
| `{album_composer}` | The composer of the entire album/work. | `Thomas Bangalter` |
| `{track_composer}` | The composer of the specific track. | `Guy-Manuel de Homem-Christo` |
| **Titles & Versions** | | |
| `{album}` / `{album_title}` | Album title (includes version like "Remastered" if present). | `Random Access Memories (Deluxe)` |
| `{album_title_base}` | Base album title strictly *without* the version details. | `Random Access Memories` |
| `{track_title}` / `{tracktitle}`| Track title (includes version if present). | `Get Lucky (Radio Edit)` |
| `{track_title_base}` | Base track title strictly *without* the version details. | `Get Lucky` |
| `{version}` / `{album_version}` | Just the version string. | `Deluxe` |
| `{version_tag}` | Smart version tag (prepends a dash: ` - Deluxe`). Leaves no trailing spaces if empty! | ` - Deluxe` |
| **Numbers & Dates** | | |
| `{track_number}` | The track number (always padded with leading zero). | `08` |
| `{disc_number}` | The disc media number (padded with leading zero). | `01` |
| `{track_count}` | Total number of tracks in the album. | `13` |
| `{disc_count}` | Total number of discs in the album. | `1` |
| `{year}` | The release year. | `2013` |
| `{release_date}` | The full original release date. | `2013-05-17` |
| **Technical Specs** | | |
| `{media_type}` | Raw product type extracted from the API (capitalized). | `Album` |
| `{quality_tag}` | Smart tag combining format and bit depth (clean MP3 fallback). | `FLAC 24` |
| `{album_url}` | The official Qobuz URL of the release. | `https://play.qobuz.com/...` |
| `{bit_depth}` | The audio bit depth. | `24` |
| `{sampling_rate}` | The audio sampling rate in kHz. | `88.2` |
| `{format}` | The downloaded file format. | `FLAC` |
| **Metadata & IDs** | | |
| `{release_type}` | Smart release type classification (`Album`, `EP`, `Single`). | `Album` |
| `{explicit}` / `{ExplicitFlag}`| Adds an `[E]` tag if parental advisory is active (empty if clean). | `[E]` |
| `{album_genre}` | Primary genre of the release. | `Electronic` |
| `{label}` | The record label name. | `Columbia` |
| `{copyright}` | Copyright string. | `℗ 2013 Daft Life` |
| `{barcode}` / `{upc}` | The global UPC/Barcode of the release. | `888837168618` |
| `{isrc}` | The unique ISRC identifier of the track. | `USSM11302305` |
| `{album_id}` / `{track_id}` | Qobuz internal database IDs. | `123456789` |

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
`”Disable OS Keyring and save tokens in config.ini?”` (Desativar Keyring do SO e salvar tokens no config.ini?)
Selecione **`yes`** se estiver em um ambiente de servidor ou NAS. Isso ignorará o chaveiro do sistema e garantirá que seus tokens persistam no arquivo `config.ini`, garantindo 100% de estabilidade na autenticação.

## 👨‍💻 Para Desenvolvedores: Usando o Qobuz-DL como Biblioteca Python

Você está criando seus próprios scripts de automação musical, bots de Telegram ou integrações com o Discord?
Você pode importar nossos mecanismos principais (Downloader Segmentado AES, Tagger Audiófilo e Cliente WAF-Bypass) diretamente em seus próprios projetos Python!

📚 **[Leia o Guia Oficial da API para Desenvolvedores em nossa Wiki](https://github.com/Sei969/qobuz-dl/wiki/Developer-Guide-(Python-API))**

## 🏆 Créditos
* **[vitiko98](https://github.com/vitiko98/qobuz-dl)**: Criador do projeto original.
* **[xwell](https://github.com/xwell/qobuz-dl)**: Pela grande reformulação do mecanismo de tags e integração com os “Goodies”.
* **[catap](https://github.com/catap)**: Pelo patch de download segmentado.
* **JosiahDanger**: Relatórios de bugs e sugestões de recursos.
* **Sorrow446 e DashLt**: O `qobuz-dl` é inspirado no projeto descontinuado Qo-DL-Reborn. Esta ferramenta utiliza o módulo principal de API `qopy`, originalmente escrito por eles.

## ⚠️ Isenção de Responsabilidade (Aviso Legal)
* Esta ferramenta foi desenvolvida para fins educacionais.
* O `qobuz-dl` não é afiliado ao Qobuz.
