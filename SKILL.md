—
name: qobuz-dl-ultra
description: Guia completo do projeto qobuz-dl-ultra (fork em Python do qobuz-dl, CLI/TUI para baixar música do Qobuz com tags, capas, letras e biblioteca local). Leia ANTES de alterar qualquer arquivo do projeto, ao corrigir bugs, adicionar comandos/flags/opções de config, mexer em tags, capas (Apple/iTunes), letras/LRC, modo interativo, testes, CI, Docker ou README. Contém mapa de módulos, convenções, decisões já tomadas, armadilhas e a regra de manutenção deste próprio arquivo.
—

# QOBUZ-DL-ULTRA — guia do projeto (SKILL.md)

> **Última revisão:** versão do pacote `2.6.5` · atualizado em 2026-10-13.
> Idioma do projeto: **português do Brasil** (código-fonte, comentários, mensagens, README, commits e testes).

—

## 0. REGRA DE MANUTENÇÃO (obrigatória)

Este arquivo é a **memória do projeto**. Ele só serve se estiver sempre igual ao código.

**A cada alteração que o dono do projeto pedir, a IA/desenvolvedor DEVE, no mesmo trabalho:**

1. Ajustar as seções afetadas deste arquivo (mapa de módulos, comandos, config, fluxos, decisões, armadilhas).
2. Acrescentar **uma linha** em [§13 Registro de alterações](#13-registro-de-alterações) (data, o que mudou, arquivos principais).
3. Se a versão do pacote mudou (`qobuz_dl/__init__.py`), atualizar o cabeçalho acima.
4. Se uma decisão do dono contradiz algo escrito aqui, **substituir** o texto antigo (não deixar as duas versões) e registrar o motivo em §10.
5. Atualizar também o `README.md` quando a mudança for visível ao usuário final (comando, flag, opção de config, comportamento).
6. Antes de entregar, conferir o checklist de [§12](#12-checklist-de-entrega).

**Não inventar.** Se algo não foi verificado no código, marcar como `(não verificado)`. Se um fato deste arquivo estiver errado, corrigir na hora.

**Como usar na prática (para a IA):** ler §1–§4 para se situar; ir direto ao módulo certo pela tabela de §3; consultar §10 (decisões) e §11 (armadilhas) **antes** de “consertar” algo que pode ser proposital.

—

## 1. Visão geral

| Item | Valor |
|:--- |:--- |
| Nome do pacote | `qobuz-dl-ultra` (import: `qobuz_dl`) |
| Comando | `qobuz-dl` (alias `qdl`; também `python -m qobuz_dl`) |
| O que é | Baixador de música do Qobuz (FLAC/MP3) com metadados ricos, ReplayGain, capas, letras (LRC), catálogo local da biblioteca e sincronização de favoritos/playlists |
| Interface | **Somente terminal**: CLI (argparse) + TUI em tela cheia (prompt_toolkit) + saída colorida. **Não há GUI web** (foi removida; ver §10) |
| Python | `>=3.10` (CI testa 3.10–3.14) |
| Licença | GPL-3.0-or-later |
| Autor / repo | Eduardo (kaduvercosa) · https://github.com/kaduvercosa/qobuz-dl-ultra |
| Origem | Fork do `qobuz-dl`; ideias de dedup/scan/sync portadas do **libsync** (arthursoares), apenas a parte Qobuz |
| Plataformas | Windows, macOS, Linux, Android, Docker/NAS e **iOS/iPadOS via a-Shell** (dono testa no iPad) |

**Princípio de portabilidade (importante):** o núcleo deve instalar em ambientes que só aceitam pacotes Python **puros** (a-Shell). Por isso `rapidfuzz`, `Pillow`, `numpy`, etc. são **extras opcionais** com degradação elegante (ver `fuzzy.py`), e `cryptography`/`Pillow` ficam **sem piso de versão** de propósito.

—

## 2. Estrutura do repositório

```
qobuz-dl-ultra/
├── qobuz_dl/            # o pacote (ver §3)
├── tests/               # pytest: unit/ ui/ lyrics/ network/ regression/ manual/
├── tools/               # scripts auxiliares (changelog, requirements, canário, diagnóstico Apple)
├── .github/workflows/   # CI (ver §9)
├── README.md            # documentação do usuário final (grande, ~50 KB)
├── SKILL.md             # ESTE arquivo
├── pyproject.toml       # fonte única de dependências, extras, pytest, ruff, mypy, coverage
├── setup.py             # shim mínimo (não duplicar dependências — há teste que garante)
├── requirements.txt     # ESPELHO gerado do pyproject (tools/gerar_requirements.py)
├── Dockerfile           # python:3.12-slim + ffmpeg; entrypoint `python -m qobuz_dl`
├── check_audio.py       # utilitário solto de verificação de áudio (raiz)
├── MANIFEST.in          # inclui README, LICENSE, requirements.txt
├── todo.md              # pendências abertas (reescrito em 2026-10-13; antes falava da GUI removida)
├── codecov.yml · .pre-commit-config.yaml · .dockerignore · .gitignore
└── blacklist_example.txt
```

—

## 3. Mapa de módulos (`qobuz_dl/`)

Tamanhos aproximados em linhas. **“Mexa aqui quando…”** é o atalho para achar o arquivo certo.

### 3.1 Entrada, comandos e configuração
| Módulo | Papel | Mexa aqui quando… |
| :--- |:--- |:--- |
| `__init__.py` | `__version__`; `main`/`Client` carregados sob demanda (evita import circular) | subir versão |
| `__main__.py` | `python -m qobuz_dl` | raramente |
| `commands.py` (~1170) | **Todos** os subcomandos e flags (argparse); parser colorido e traduzido p/ PT; `qobuz_dl_args()` monta o parser | criar/alterar comando ou flag |
| `cli.py` (~1700) | `main()` → `async_main()`: config.ini, assistente de 1ª execução (`_reset_config`), login/token/keyring, `auth`, `_handle_commands` (roteamento), logo/boas-vindas, checagem de atualização | roteamento de comando, config.ini, login |
| `settings.py` (~350) | `QobuzDLSettings`: junta **CLI + config.ini** num só objeto (`from_arguments_configparser`). Helpers `_merge_bool_opt_in/out` | nova opção que existe na CLI **e** no config |
| `paths.py` | Valida/cria a pasta de biblioteca (`directory`) testando escrita real | pasta de downloads/permissões |
| `constants.py` | Padrões de `folder_format`/`track_format` | formato padrão de pastas/arquivos |
| `exceptions.py` | Exceções próprias | novo tipo de erro |
| `home_screen.py` | Tela inicial: lê comandos/flags **do parser real** (comando novo aparece sozinho); só `USO`/`EXEMPLO` são escritos à mão | texto de ajuda por comando |

### 3.2 API Qobuz e download
| Módulo | Papel | Mexa aqui quando… |
| :—|:—|:—|
| `qopy.py` (~1270) | Cliente **assíncrono** da API (`Client.create`, `api_call`, `get_track_url`), assinatura/criptografia, fallback de qualidade | endpoints, autenticação, parsing de resposta |
| `bundle.py` | Extrai `app_id`/segredos do `bundle.js` do Web Player (baseado em Spoofbuz). Pode quebrar quando o Qobuz muda o site | erro de app_id/secret |
| `downloader.py` (~3130) | Classe `Download`: álbum/faixa/playlist, fallback de qualidade, download segmentado (bypass Akamai), capa, booklet, letras, nomes de arquivo/pasta. **Ponto de entrada:** `Download(...).download_id_by_type(...)` | fluxo de download |
| `download_utils.py` | Funções puras extraídas do `downloader.py` (quebra em módulos menores em andamento) | helper de download testável |
| `http_download.py` (~650) | Download HTTP **retomável e verificado** (capas, PDFs, áudio). Não toca no caminho segmentado/criptografado | corte de conexão, `Content-Length`, SSL |
| `fileio.py` | Escrita **atômica**, checksums, limpeza de temporários | gravar arquivo sem risco de ficar pela metade |
| `track_identity.py` | Identidade de faixa por **ID Qobuz** (não por nome): evita colisão artista+título em playlists planas | nomes duplicados em playlist |
| `core.py` (~2875) | “Motor”: TUI de seleção em tela cheia + classe `QobuzDL` (`handle_url`, `interactive`, busca, lotes, Last.fm, playlists) | modo interativo, orquestração |
| `interactive_ui.py` | Tema e helpers **puros** de layout da TUI (`_shade`, `_align_text`, `_get_table_layout`) | tabela/colunas do interativo |
| `search_prompt.py` | Tela “NOVA PESQUISA” (campo dentro de caixa arredondada) | tela de busca |
| `playlist_import.py` | Lê TXT/CSV/JSON de qualquer plataforma → `{artist,title}` → fuzzy match com Qobuz | importar playlist |
| `sync_playlist.py` | Sincronização bidirecional playlist Qobuz ⇄ pasta local (+ `.m3u`) | `sync-playlist` |

### 3.3 Metadados, capas e letras
| Módulo | Papel | Mexa aqui quando… |
|:--- |:--- |:--- |
| `metadata.py` (~1470) | `_get_tags_to_add()` cria tags; `tag_flac()`/`tag_mp3()` gravam. Capas embutidas, gêneros, IDs Qobuz, ReplayGain, tags clássicas. **Multi-tags** (`_split_multi_value`, `_split_protegido`) | qualquer tag |
| `retro_tags.py` (~1290) | Comando `tags`: regrava tags de arquivos **já baixados** a partir do Qobuz; troca capa quando a Apple/iTunes valida uma melhor. Modos `—tags-only` / `—covers-only` / `—both`. Mostra diff das tags | correção retroativa, capa Apple no `tags` |
| `retro_tagger.py` | Inspeção/atualização retroativa de **letras** (comando `lyrics`) | letras retroativas |
| `lyrics_engine.py` (~840) | `LyricsEngine`: busca, normaliza, faz fallback e grava letras. Ordem em `PROVIDERS`: **Qobuz → BiniLyrics → Musixmatch → LRCLIB → Genius** | nova fonte, LRC, bilíngue |
| `binilyrics.py` | Fonte BiniLyrics (TTML estilo Apple Music). **Módulo puro (sem rede)**; a rede fica em `LyricsEngine._provider_binilyrics` | LRC por palavra |
| `musicbrainz.py` | Enriquecimento por **ISRC** (`MUSICBRAINZ_*`) | IDs MusicBrainz |
| `utils.py` (~2020) | Miscelânea grande: `.m3u`, filtros de discografia, durações, binários externos (ffmpeg/fpcalc), integridade de áudio, nomes de arquivo, `get_config_paths()`, **busca de capa Apple/iTunes** (`get_apple_hq_cover`, `apple_release_kind`) | helper genérico; capa Apple |

### 3.4 Biblioteca local, banco e relatórios
| Módulo | Papel | Mexa aqui quando… |
|:--- |:--- |:--- |
| `db.py` | `qobuz_dl.db`, tabela `downloads` (PK `(id, quality)`), com migração | dedup de downloads |
| `library_db.py` | `library.db`: tabelas `albums` + `sync_runs` (favoritos + estado local) | catálogo local |
| `library_scan.py` (~820) | `scan`: casa pastas do disco com o catálogo (auto / revisar / sem-match) | casamento pasta ↔ álbum |
| `favorites_sync.py` | `sync-favorites`: conta Qobuz ⇄ catálogo local (diff, histórico, baixar o que falta) | favoritos |
| `library_cmd.py` | Terminal dos comandos `sync-favorites`, `scan`, `library` (cada `cmd_*` devolve **código de saída**, não chama `sys.exit`) | UX desses comandos |
| `sentinel.py` | Sentinela por álbum **`.streamrip.json`** (nome herdado do libsync de propósito): dedup pelo sistema de arquivos | dedup por pasta |
| `sync.py` | `—sync-db` (reconstrói o banco varrendo a biblioteca) e `—find-duplicates` (fingerprint Chromaprint/AcoustID) | reconstruir banco / duplicatas |
| `postprocess.py` | Relatório de downloads: **`.report.json` oculto** (fonte da verdade, sob lock por pasta), `collection_index.jsonl`, `Tracklist.txt` | relatório pós-download |
| `report_viewer.py` | Gera página HTML autônoma (estética “Nothing”) a partir do `.report.json` | visual do relatório |
| `watcher.py` | `—watch`: retro-tagging automático quando aparecem arquivos novos (lib `watchdog`) | modo vigia |
| `inspector.py` (~1150) | `inspect`: navegador de arquivos em tela cheia + relatório de genuinidade/gráfico espectral | inspeção de áudio |
| `doctor.py` | `doctor`: checagem **somente leitura** do ambiente/config/bancos (herdado dos scripts do libsync) | diagnóstico |
| `stats_view.py` | Renderização do `stats` | estatísticas |

### 3.5 Camada de terminal
| Módulo | Papel |
| :— |:—|
| `ui.py` (~750) | **Única** saída de tela: `ui.emit()` com um lock e uma fonte de verdade p/ largura/breakpoints. Evita `print`/`logging`/`tqdm` brigando |
| `color.py` | ANSI/TrueColor, `NO_COLOR`/`FORCE_COLOR`, TTY, cor de destaque (accent) |
| `fuzzy.py` | Similaridade de texto (usa `rapidfuzz` se existir; senão `difflib`) |

—

## 4. Comandos da CLI

Definidos em `commands.py`, roteados em `cli.py::_handle_commands`. `DOWNLOAD_COMMANDS` (em `cli.py`) agrupa os que precisam de login/cliente Qobuz.

| Comando | Aliases | Função |
| :--- |:--- |:--- |
| `interactive` | `i`, `fun` | Busca interativa (TUI), menu de tipo (faixa/álbum/artista…) |
| `dl` | — | Download por URL (álbum/faixa/artista/gravadora/playlist) ou arquivo `.txt` de URLs (marca `[DONE]`, retoma) |
| `lucky` | — | Baixa os N primeiros resultados de uma busca |
| `import-playlist` | `ip` | Importa playlist de TXT/CSV/JSON/Last.fm |
| `sync-playlist` | `sp` | Sincroniza playlist Qobuz ⇄ pasta local |
| `sync-favorites` | `sf` | Favoritos da conta ⇄ catálogo local |
| `scan` | `library-scan` | Casa pastas de álbum com o catálogo |
| `library` | `lib` | Consulta/gerencia o catálogo local |
| `lyrics` | — | Letras retroativas |
| `tags` | — | Tags/capas retroativas (`—tags-only`, `—covers-only`, `—both`, `—multi-tags`/`—no-multi-tags`) |
| `inspect` | — | Inspeciona arquivo de áudio |
| `stats` | — | Estatísticas (`—artistas` etc.) |
| `auth` | — | Atualiza as credenciais de login |
| `user` | — | Mostra informações do usuário e status da assinatura |
| `doctor` | `check` | Saúde do ambiente |

**Flags globais** (sem subcomando): `-r/—reset`, `-p/—purge`, `—sync-db [PATH]`, `—find-duplicates [PATH]`, `—watch [PATH]`, `-sc/—show-config`, mais `add_output_args` (saída/cores/progresso).

—

## 5. Fluxos principais

### 5.1 Download (`dl`)
`cli.async_main` → lê config.ini + token (keyring/config) → `QobuzDLSettings` → `core.QobuzDL.handle_url` → `downloader.Download.download_id_by_type` → URL de stream (`qopy.get_track_url`, com fallback de qualidade) → `http_download` (ou segmentado) → `metadata.tag_flac/tag_mp3` → capa/booklet/letras → `postprocess` (`.report.json`, sentinela, `.m3u`, banco `downloads`).

### 5.2 Tags retroativas (`tags`)
`retro_tags.py`: varre arquivos → lê IDs Qobuz das tags → busca metadados atuais → compara e grava (mostra **diff por arquivo**) → opcionalmente busca capa Apple/iTunes validada (`utils.get_apple_hq_cover` + validação por correspondência de texto) e registra `Capa: Apple/iTunes` no comentário. Se a Apple não validar, a capa embutida é **preservada**.

### 5.3 Letras
`lyrics_engine.LyricsEngine.fetch_and_inject()` percorre `PROVIDERS` e usa a primeira letra encontrada. Grava `.lrc` (se não desligado) e/ou embute nas tags. LRC bilíngue em `_build_bilingual_lrc`. LRC por palavra (`—lyrics-word-sync`) vem do BiniLyrics.

### 5.4 Biblioteca local
`sync-favorites` preenche `library.db` → `scan` casa pastas com álbuns (por ID de tag primeiro, depois heurística) e grava sentinelas → `library` consulta. `—sync-db` reconstrói `qobuz_dl.db` a partir do disco.

—

## 6. Configuração, arquivos e variáveis

**Diretório de config** (`utils.get_config_paths()`), nesta ordem: `CONFIG_DIR` → `QOBUZ_DL_IOS_HOME` → detecção automática de iOS/a-Shell (`HOME` contém `Containers/Data/Application` ⇒ `~/Documents`) → `platformdirs.user_config_dir()`. Dentro dele, pasta `qobuz-dl/`:

| Arquivo | Conteúdo |
|:---|:---|
| `config.ini` | Seção `[qobuz]` (ou `[DEFAULT]` se não houver `[qobuz]`). Token em `user_token` **e** `user_auth_token` (mantidos sincronizados) ou no **Keyring** do SO |
| `qobuz_dl.db` | Dedup de downloads (`downloads`, PK `id+quality`) |
| `library.db` | Catálogo da biblioteca (`albums`, `sync_runs`) |

**Arquivos gerados dentro da música baixada:** `.report.json` (oculto), `.streamrip.json` (sentinela), `collection_index.jsonl`, `Tracklist.txt`, `.m3u`, `.lrc`, `cover.jpg`/booklet.

**Variáveis de ambiente lidas:** `CONFIG_DIR`, `QOBUZ_DL_IOS_HOME`, `NO_COLOR`, `FORCE_COLOR`, `COLUMNS`, `TERM`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `APPDIR` (a-Shell: procura ffmpeg em `$APPDIR/bin`), `QOBUZ_DL_STARTUP_TIMING`, e as de plataforma (`APPDATA`, `ANDROID_*`, `HOME`).

**Opções de config relevantes (leitura em `settings.py`):** `directory`, `write_sentinel`, `no_lrc_files`, `embed_lyrics`, `fetch_translation`, `only_synced_lyrics`, `lyrics_word_sync`, `artists_with_comma` (lista com `;`), `multi_value_tags`, além das de formato/qualidade/capa descritas no README. Precedência: **flag da CLI > config.ini > padrão**.

—

## 7. Convenções de código

- **Idioma:** tudo em português (comentários, docstrings, mensagens ao usuário, nomes de teste). Identificadores técnicos podem ser em inglês.
- **Cabeçalho de módulo:** comentário “MÓDULO / DESCRIÇÃO / ONDE PROCURAR quando precisar mexer em algo”. Manter ao criar módulo novo.
- **Toda saída de tela passa por `ui.emit()`** (nunca `print` cru no fluxo normal) — o lock evita corrupção de linha com `tqdm`/logging.
- **Cores:** usar `qobuz_dl.color` (respeita `NO_COLOR`). Aliases: `INFO`=azul-claro, `WARNING`=amarelo.
- **Assíncrono:** rede e downloads são `async` (httpx/asyncio). Não bloquear o loop.
- **Escrita de arquivo:** usar `fileio` (atômica) para qualquer arquivo que não possa ficar pela metade.
- **Dependência opcional:** importar com `try/except ImportError` e degradar (marcar o bloco com `# pragma: no cover`). Não colocar dependência compilada no núcleo (há teste em `tests/regression/test_dependencias.py`).
- **Funções `cmd_*`** devolvem código de saída; só o roteador chama `sys.exit`.
- **Lint:** Ruff, `line-length = 88` (E501 ignorado), seleção `E,W,F,B,C,T`. `target-version = py312` mas o código deve rodar em 3.10.
- **Tipos:** mypy permissivo com **baseline** (só reclama de erro novo).
- **Commits:** mensagens em português, prefixos `update:`, `fix:`, `style:`; commits `style: correções automáticas do Ruff` são do bot.

—

## 8. Testes

- Rodar tudo: `pip install -e “.[all,dev]”` e `pytest`. Rápido: `pytest -m “not slow”`.
- Pastas: `tests/unit/` (maioria; lógica pura), `tests/ui/` (largura, quebra, concorrência, tela inicial), `tests/lyrics/`, `tests/network/` (HTTP **mockado** via pytest-httpx), `tests/regression/` (trava bugs históricos/dependências), `tests/manual/` (**não** roda no pytest; exige credenciais reais).
- Marcadores (`—strict-markers`): `slow`, `integration`, `asyncio`, `unit`, `network`, `ui`, `regression`, `lyrics`. `asyncio_mode = auto`.
- **`tests/conftest.py` isola o ambiente:** define `CONFIG_DIR` temporário e `NO_COLOR=1` **antes** de importar `qobuz_dl`, e cria um `config.ini` com credenciais **inválidas de propósito** (nenhum teste toca a API real; evita o assistente de 1ª execução travar em `input()`).
- Regra do projeto: bug corrigido ⇒ **teste de regressão** que falha antes e passa depois.
- Cobertura: `fail_under = 75` no `pyproject.toml`, mas o CI usa `—cov-fail-under=60` (ver §11).

—

## 9. CI, build e release

| Workflow | Função |
| :--- |:--- |
| `tests.yml` | pytest em ubuntu (py3.10–3.14) + macOS (3.13); cobertura em ubuntu/3.12; job de **mypy com baseline** |
| `ruff.yml` / `ruff-autofix.yml` | Lint e autofix (gera os commits “correções automáticas do Ruff”) |
| `canary.yaml` | “Canário” diário da API do Qobuz (`tools/canary_check*.py`) — avisa quando o Qobuz muda algo |
| `changelog.yml` | Gera changelog em tags (`tools/gerar_changelog.py`, formato Keep a Changelog) |
| `pypi.yml` | Publica no PyPI em *release* |
| `docker.yml` | Build/publica imagem Docker |
| `dependabot*.yml` | Atualizações e auto-merge |

**Fonte única de dependências = `pyproject.toml`.** Depois de mudar dependências: `python tools/gerar_requirements.py` (atualiza `requirements.txt`). O Docker instala direto do pyproject (extras `speed,covers,watch,genius,analyze`).

**Extras:** `speed` (rapidfuzz, brotli), `covers` (Pillow), `watch` (watchdog), `duplicates` (pyacoustid), `genius`, `analyze` (numpy), `all`, `dev`, `dev-full`.

**Subir versão:** editar `qobuz_dl/__version__`; o commit costuma ser `Bump version to X.Y.Z`.

**`tools/`:** `apple_cover_check.py`, `apple_porque.py`, `apple_text_compare.py` (diagnóstico da correspondência de capa Apple — explicam *por que* uma faixa foi aceita/rejeitada); `canary_check*.py`; `gerar_changelog.py`; `gerar_requirements.py`.

—

## 10. Decisões do dono e bugs já resolvidos (não desfazer sem pedir)

| Tema | Decisão / estado |
| :--- | :--- |
| **GUI web** | Foi tentada (FastAPI + `web_ui/`, estilo “Nothing”) e **retirada completamente do projeto** em 2026-10-06. Não recriar nem referenciar. Resíduo: só menções em comentários de código |
| **Modo interativo** | Fica o **visual clássico**; protótipo “rich” descartado. Tela de busca dentro de caixa. `qobuz-dl i` mantém o menu de tipo e tudo que já tinha. Testado no iPad (a-Shell) |
| **LRC bilíngue** | Original e tradução com o **mesmo timestamp** (sem deslocamento de ~20 ms). Implementado em `_build_bilingual_lrc` (“mesmo timestamp”). Players diferentes exibem diferente — decisão do dono é manter igual |
| **multi_tags** | Com `multi_value_tags`, campos multivalorados (artista, compositor, gênero…) viram **tags de verdade** (uma entrada por valor), não uma string com `;`. Expectativa: player mostrar separado por **vírgula**. Nomes que contêm vírgula (ex.: “Tyler, The Creator”) são protegidos por lista embutida + `artists_with_comma` |
| **Capa Apple/iTunes no `tags`** | Se a Apple valida uma capa, ela substitui a embutida e é registrada no comentário; se não, a arte existente é preservada. Bug reportado em 2026-10-08: faixa certa encontrada mas **rejeitada** (colaborações — a Qobuz guarda só o artista principal — e títulos com versão/feat.). Há testes cobrindo esses casos em `test_utils_apple_match.py` (`feat.`/`ft.`/`with`, “Radio Edit/Live/Remaster” continuam no título); estado final do fix em produção: não verificado |
| **Capa não embutida (downloads novos)** | Reportado pelo dono: acontecia em downloads **novos**, não só antigos. Há regressões em `test_downloader_cover_source.py` (um `NameError` por `cover_source` fora do escopo em toda faixa, e a origem gravada nas tags era fixa em “Qobuz” mesmo com capa Apple; `_get_cover_and_embed` agora devolve a origem real) e em `test_metadata_replace_cover.py`. Se voltar a ocorrer, começar por esses dois pontos |
| **Pasta da biblioteca** | Ao trocar, testar leitura/escrita; criar se não existir, independentemente de onde o programa está instalado (`paths.py`) |
| **Sentinela `.streamrip.json`** | Nome mantido por compatibilidade com libsync/streamrip |
| **Portabilidade a-Shell** | Sem dependência compilada no núcleo; `fuzzy.py` cai para `difflib`; Pillow sem piso de versão |

—

## 11. Armadilhas e pendências conhecidas

Pendências abertas também estão em `todo.md`.

1. **Cobertura mínima inconsistente:** `pyproject.toml` (`fail_under = 75`) e `codecov.yml` (alvo 75%) pedem 75%; o CI roda com `—cov-fail-under=60`. Medir a cobertura real e alinhar os três. *(decisão do dono)*
2. **`mypy-baseline.txt` não versionado:** o CI gera um novo a cada execução quando ele não existe, então hoje o mypy não detecta erro *novo*. Gerar uma vez e fazer commit.
3. **Teclado do iPad (Pontuação Inteligente do iOS):** ao editar `README.md` ou `SKILL.md` pelo iPad, `—` vira `—` e aspas retas viram curvas; isso quebrou comandos (já corrigido no README em 2026-10-13). Desligar em Ajustes > Geral > Teclado e **conferir flags dentro de blocos de código** depois de editar. Em arquivos `.py`, `.toml` e `.yml` o mesmo problema seria erro de sintaxe.
4. **README — linha `usage` dos comandos globais desatualizada:** não lista `tags`, `inspect`, `import-playlist`, `sync-favorites`, `scan`, `library`, `doctor`, `auth`, `user`.
5. **Arquivos grandes:** `core.py` (~2900), `downloader.py` (~3100), `utils.py` (~2000). A quebra em módulos menores está em andamento (`download_utils.py`, `interactive_ui.py`, `stats_view.py` já extraídos). Ao extrair, mover **só** o que já tem teste.
6. **`bundle.py`** depende do HTML/JS do Qobuz: quebra sem aviso quando o site muda (o canário existe para isso).
7. **Testes placeholder:** `tests/unit/test_core_remaining_interactive_disabled.py` só tem testes `skip` (`pass`) criados para o iOS; a cobertura real do `interactive()` está em `test_core_interactive_branches.py` e testes estáticos.
8. **Zip do projeto:** ao compactar para enviar, excluir `.coverage` e `.pytest_cache/` (estão no `.gitignore`, mas foram no zip e pesam ~3 MB).

**Resolvidas em 2026-10-13** (não reabrir): `qobuz_dl/pyproject.toml` duplicado removido; teste `test_core_interactive_branches-2026-09-28.py` renomeado; `todo.md` reescrito; README com `—`/aspas curvas dentro de comandos corrigido.

—

## 12. Checklist de entrega

- [ ] Mudança feita no módulo certo (§3), sem duplicar lógica.
- [ ] Mensagens/comentários em português.
- [ ] Saída de tela via `ui.emit()`; cores via `color`.
- [ ] Teste novo/ajustado (regressão se for bug). `pytest -m “not slow”` verde.
- [ ] `ruff check` sem erros novos.
- [ ] Dependência nova? Entrou no `pyproject.toml` **e** rodou `tools/gerar_requirements.py`; núcleo continua puro-Python.
- [ ] Opção nova de config/flag? Entrou em `commands.py`, `settings.py`, `home_screen.py` (uso/exemplo) e README.
- [ ] README atualizado se o usuário final percebe a mudança.
- [ ] **Este SKILL.md atualizado + linha no §13** (§0).

—

## 13. Registro de alterações

Mais recente primeiro. Uma linha por alteração pedida.

| Data | Alteração | Arquivos principais |
| :--- |:--- |:---|
| 2026-10-13 | Criação do SKILL.md com base na leitura do projeto (v2.6.5): mapa de módulos, comandos, fluxos, config, convenções, testes/CI, decisões e armadilhas | `SKILL.md` |
| 2026-10-13 | Resolvidas as pendências do §11: README corrigido (`—` e aspas retas nos comandos), `qobuz_dl/pyproject.toml` duplicado removido, teste renomeado para `test_core_interactive_branches.py`, `todo.md` reescrito; SKILL.md restaurado (frontmatter e flags tinham sido alterados pela Pontuação Inteligente) e §11 reorganizado | `README.md`, `todo.md`, `pyproject.toml`, `SKILL.md`, `tests/unit/` |
| 2026-10-13 | Pausa instrumental `• • •` passa a ficar 0,5 s **antes da próxima linha** (era 0,5 s depois do início da anterior); testes atualizados e 2 novos (par bilíngue, lacuna de exatamente 10 s) | `qobuz_dl/lyrics_engine.py`, `tests/unit/test_lyrics_lrc_format.py` |