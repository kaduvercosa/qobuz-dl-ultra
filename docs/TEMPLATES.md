# Templates de pasta e de arquivo

Guia das variáveis de `folder_format`, `track_format` e companheiros, conferido no
código (`downloader.py`, `download_utils.py`, `constants.py`). O `README.md` tem
mais exemplos; aqui está a lista completa e as regras.

## Onde configurar

| `config.ini` | Flag | Padrão | Para quê |
|:--- |:--- |:--- |:--- |
| `folder_format` | `-ff`, `--folder-format` | `{release_type}/{album_artist} - {album_title} ({year}) [{quality_tag}]` | caminho da pasta do álbum |
| `track_format` | `-tf`, `--track-format` | `{track_number}. {track_title_base} {explicit}` | nome do arquivo |
| `fallback_folder_format` | `-fbff`, `--fallback-folder-format` | mesmo padrão do `folder_format` | usado quando o principal falha |
| `multiple_disc_track_format` | `--multiple-disc-track-format` | `{disc_number}.{track_number} - {track_title_base}` | nome do arquivo com `multiple_disc_one_dir` |

Para reaplicar os formatos a arquivos que já estão no disco, sem baixar de novo:
`qobuz-dl organize` (veja `--dry-run` primeiro).

## Regras

- É o `str.format` do Python: `{variavel}`. Para chaves literais, dobre-as:
  `{{{barcode}}}` vira `{1234567890123}`.
- `/` no `folder_format` cria subpastas: `{release_type}/{album_artist} - {album_title}`
  dá `Album/Daft Punk - Discovery`. O `track_format` é só o nome do arquivo.
- Cada parte do caminho é limpa (caracteres que o sistema não aceita viram `_`) e,
  se passar de 120 caracteres, é encurtada no meio com `...`.
- **Folder e faixa usam conjuntos DIFERENTES de variáveis** (tabelas abaixo). Uma
  variável que só existe no álbum (`{label}`, `{upc}`, `{quality_tag}`) **não existe**
  no `track_format`; usá-la lá dá erro de formato e o programa cai no padrão.
- `{track_number}` e `{disc_number}` já são **texto com dois dígitos** (`"02"`).
  Especificação numérica como `{track_number:02d}` não funciona (dá erro); use
  `{track_number}`.
- Variável desconhecida aparece como aviso de configuração na inicialização.

## Variáveis do `folder_format` (e do `fallback_folder_format`)

| Variável | Conteúdo |
|:--- |:--- |
| `{album_artist}` | artista do álbum (vários artistas juntos) |
| `{artist}` | artista principal conforme o Qobuz |
| `{album_title}` | título **com a versão** entre parênteses, se ela ainda não está no título: `Rare (Deluxe)` |
| `{album_title_base}` | título sem a versão |
| `{album}` | o mesmo que `{album_title}` |
| `{album_version}` | versão (`Deluxe`, `Live in Heidelberg`) ou vazio |
| `{version_tag}` | ` - <versão>` ou vazio |
| `{album_id}`, `{album_url}` | id e link do álbum no Qobuz |
| `{album_genre}`, `{album_composer}` | gênero e compositor |
| `{label}` | gravadora (`;`, `/` e ` - ` viram ` / `) |
| `{copyright}` | texto de copyright |
| `{upc}`, `{barcode}` | código de barras (iguais) |
| `{release_date}`, `{year}` | `2024-05-17` e `2024` |
| `{release_type}`, `{media_type}` | Album, EP, Single... (classificação pelo número de faixas; iguais) |
| `{format}` | `FLAC` ou `MP3` |
| `{bit_depth}`, `{sampling_rate}` | `24` e `96` (kHz) |
| `{quality_tag}` | `FLAC 24` (FLAC) ou `MP3` |
| `{disc_count}`, `{track_count}` | número de discos e de faixas |
| `{explicit}`, `{ExplicitFlag}` | `🅴` se o álbum tem aviso parental, senão vazio (iguais) |

## Variáveis do `track_format` (e do `multiple_disc_track_format`)

| Variável | Conteúdo |
|:--- |:--- |
| `{track_number}` | `01`, `02`... (por disco, a menos que `continuous_track_numbers` mude as tags; o nome do arquivo continua por disco) |
| `{disc_number}` | `01`, `02`... |
| `{track_title}` | título com a versão: `Faixa (Remix)` |
| `{track_title_base}` | título sem a versão |
| `{tracktitle}` | o mesmo que `{track_title}` |
| `{version}` | versão da faixa ou vazio |
| `{track_artist}`, `{artist}` | artista da faixa |
| `{album_artist}`, `{albumartist}` | artista do álbum |
| `{track_composer}` | compositor da faixa |
| `{track_id}` | id da faixa no Qobuz |
| `{isrc}` | ISRC |
| `{album_title}`, `{album_title_base}` | do álbum |
| `{year}`, `{release_date}` | da faixa |
| `{bit_depth}`, `{sampling_rate}` | máximos da faixa |
| `{explicit}`, `{ExplicitFlag}` | `🅴` se a **faixa** tem aviso parental |

## Receitas

```ini
# Por gênero e artista
folder_format = {album_genre}/{album_artist}/{album_artist} - {album_title_base}{version_tag} ({year}) [{bit_depth}B-{sampling_rate}kHz]

# Tipo de lançamento como pasta, qualidade no nome (o padrão do programa)
folder_format = {release_type}/{album_artist} - {album_title} ({year}) [{quality_tag}]

# Com código de barras, para casar com outros catálogos
folder_format = {album_artist} - {album_title} ({year}) {{{barcode}}}

# Arquivo com artista da faixa (útil em coletâneas)
track_format = {track_number}. {track_title_base} - {track_artist}

# Arquivo com ISRC
track_format = {track_number} - {track_title} [{isrc}]

# Vários discos numa pasta só (com multiple_disc_one_dir = true)
multiple_disc_track_format = {disc_number}.{track_number} - {track_title_base}
```

## Pegadinhas conhecidas

- O `README.md` mostra o marcador de conteúdo explícito como `[E]`, mas o código
  grava `🅴`. Vale o código.
- Em `track_format`, `{albumartist}` funciona, mas o aviso de configuração da
  inicialização o chama de "desconhecido" (a lista de nomes aceitos em
  `cli.validate_config_formats` não o tem). É só o aviso; o nome sai certo.
- `{album_title}` já inclui a versão. Para controlar onde a versão aparece, use
  `{album_title_base}{version_tag}`; `{album_title}{version_tag}` repete a versão
  quando ela não estava no título (conferido pela leitura do código, não rodando).
