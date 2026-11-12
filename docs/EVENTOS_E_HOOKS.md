# Eventos em JSON e hooks pós-download

Tudo aqui é **opcional e desligado por padrão**. Serve para ligar o programa a um
painel, a um Atalho do iOS, a um script, ao seu servidor de música ou ao celular
(notificação), sem interface web.

- [Eventos em JSON (`--events`)](#eventos-em-json---events)
- [Hooks: comando, webhook e ntfy](#hooks-comando-webhook-e-ntfy)
- [Reescanear o servidor de música (`--rescan`)](#reescanear-o-servidor-de-música---rescan)
- [Configuração (flags, ambiente e `config.ini`)](#configuração)
- [Limites e comportamento](#limites-e-comportamento)

Vale para os comandos que baixam: `dl`, `lucky`, `interactive`, `sync-playlist`,
`import-playlist`, `sync-favorites` e `retry`. Os outros (`config`, `stats`,
`tags`...) nunca disparam hooks.

## Eventos em JSON (`--events`)

```
qobuz-dl dl URL --events eventos.ndjson      # um arquivo, uma linha por evento
qobuz-dl dl URL --events -                   # no stderr (o stdout é da tela)
tail -f eventos.ndjson | jq -c 'select(.evento=="faixa")'
```

O arquivo é aberto em modo *append* e cada linha é gravada na hora. Se o destino
não puder ser aberto ou der erro de escrita, os eventos desligam com um aviso e o
download continua.

### Relação com `--progress-json`

`--progress-json` já existia: escreve no **stdout** só os eventos por faixa
(`track_done`, `track_failed`...), misturado com a tela. `--events` é outro canal,
que não mexe nele:

| | `--progress-json` | `--events` |
|:--- |:--- |:--- |
| Destino | stdout | arquivo ou stderr |
| Nível | faixa | faixa, lançamento (álbum/faixa avulsa/playlist) e sessão |
| Versão do formato | não | campo `v` |
| Alimenta os hooks | não | sim |

### Formato

Cada linha é um objeto com `v` (versão do formato, hoje `1`), `ts` (ISO 8601 em
UTC) e `evento`. Campos novos podem aparecer; um campo existente só muda de
significado com um `v` novo.

| `evento` | Quando | Campos |
|:--- |:--- |:--- |
| `sessao_iniciada` | começo | `comando`, `versao` |
| `lancamento_iniciado` | primeira vez que uma pasta entra no relatório (uma por pasta e por execução) | `pasta`, `tipo`, `titulo`, `artista`, `id`, `faixas` |
| `faixa` | cada mudança de estado de uma faixa | `pasta`, `numero`, `id`, `titulo`, `artista`, `situacao`, `motivo`, `nova` |
| `lancamento_concluido` | fim de um álbum, faixa avulsa ou playlist | `pasta`, `tipo`, `tipo_lancamento`, `titulo`, `artista`, `id`, `url`, `completo`, `resumo`, `faixas_novas`, `qualidade` |
| `sessao_concluida` | fim | `ok`, `interrompida`, `duracao_s`, `lancamentos`, `lancamentos_incompletos`, `faixas_novas`, `faixas_falhas`, `faixas_puladas` |

- `situacao` da faixa: `concluido`, `pulada`, `falha`.
- `nova` é `true` só para faixa baixada **agora**. Faixa que já estava no disco
  (`motivo` "Arquivo já existente") é `concluido` com `nova: false`.
- `resumo` vem do `.report.json`: `total`, `concluidas`, `puladas`, `falhas`, `pendentes`.
- Durante o download a pasta do álbum tem o prefixo `[IN PROGRESS]`. Os eventos
  `lancamento_iniciado` e `faixa` trazem esse caminho; `lancamento_concluido` traz
  o caminho final (ou `[INCOMPLETE] ...` se faltou faixa). Para juntar tudo de um
  álbum, ignore esses prefixos.

Exemplo (linhas encurtadas):

```json
{"v":1,"ts":"2026-11-10T12:00:00.123+00:00","evento":"sessao_iniciada","comando":"dl","versao":"2.6.8"}
{"v":1,"ts":"2026-11-10T12:00:03.410+00:00","evento":"faixa","pasta":"/m/[IN PROGRESS] Album","numero":"01","id":123,"titulo":"Faixa","situacao":"concluido","motivo":"","nova":true}
{"v":1,"ts":"2026-11-10T12:04:10.002+00:00","evento":"lancamento_concluido","titulo":"Album","completo":true,"resumo":{"total":10,"concluidas":10,"puladas":0,"falhas":0,"pendentes":0},"faixas_novas":10}
{"v":1,"ts":"2026-11-10T12:04:10.050+00:00","evento":"sessao_concluida","ok":true,"duracao_s":250.1,"lancamentos":1,"faixas_novas":10,"faixas_falhas":0}
```

## Hooks: comando, webhook e ntfy

| Flag | Quando dispara |
|:--- |:--- |
| `--on-complete COMANDO` | a cada lançamento concluído sem faltas |
| `--on-error COMANDO` | a cada lançamento que terminou incompleto |
| `--on-finish COMANDO` | uma vez, no fim, se algum lançamento foi processado |
| `--webhook URL` | POST em JSON: por padrão `lancamento_concluido` e `sessao_concluida` |
| `--ntfy TOPICO_OU_URL` | notificação: por padrão só `sessao_concluida` |
| `--rescan SERVIDORES` | uma vez, no fim, e só se baixou arquivo novo |

### Comandos

O comando roda no **shell**, um de cada vez e na ordem, dentro da pasta do
lançamento (quando ela existe). Os dados chegam em **variáveis de ambiente** e o
evento inteiro em JSON no **stdin**. Nada é colado no texto do comando: um título
com aspas, `;` ou `$(...)` não executa nada, desde que você use a variável entre
aspas (`"$QOBUZ_DL_TITULO"`).

| Variável | Conteúdo |
|:--- |:--- |
| `QOBUZ_DL_EVENTO` | nome do evento |
| `QOBUZ_DL_TITULO`, `QOBUZ_DL_ARTISTA` | do lançamento |
| `QOBUZ_DL_PASTA`, `QOBUZ_DL_ID`, `QOBUZ_DL_URL` | pasta, id e link do Qobuz |
| `QOBUZ_DL_TIPO` | Album, EP, Single, Playlist... |
| `QOBUZ_DL_COMPLETO` | `1` ou `0` |
| `QOBUZ_DL_QUALIDADE` | por exemplo `FLAC 24/96` |
| `QOBUZ_DL_FAIXAS_TOTAL`, `_OK`, `_FALHAS`, `_PULADAS`, `_NOVAS` | contagens |
| `QOBUZ_DL_OK`, `QOBUZ_DL_LANCAMENTOS`, `QOBUZ_DL_DURACAO_S` | só no fim da sessão |
| `QOBUZ_DL_JSON` | o evento inteiro |

Exemplos:

```
# aviso no Linux, por álbum
qobuz-dl dl URL --on-complete 'notify-send "$QOBUZ_DL_ARTISTA" "$QOBUZ_DL_TITULO"'

# copiar só o que é novo (FAIXAS_NOVAS > 0) para o NAS
qobuz-dl dl URL --on-complete '[ "$QOBUZ_DL_FAIXAS_NOVAS" -gt 0 ] && rsync -a ./ nas:/musica/"$QOBUZ_DL_TITULO"/'

# ler o evento pelo stdin
qobuz-dl dl URL --on-finish 'jq -r ".faixas_novas" >> ~/novas.log'
```

Prazo por comando: 60 segundos (`hook_timeout`). Passou disso, o comando é
encerrado e aparece um aviso. Código de saída diferente de 0 também vira aviso e
não interrompe nada.

> **iPhone/iPad (a-Shell)**: o a-Shell não cria processos, então `--on-*` não
> funciona lá (o programa avisa uma vez). Use `--webhook`, `--ntfy` ou `--events`.

### Webhook

POST com `Content-Type: application/json` e o evento no corpo. Com
`webhook_secret`, o corpo é assinado (HMAC-SHA256) no cabeçalho
`X-Qobuz-DL-Signature: sha256=<hex>`. Para conferir no receptor:

```python
import hashlib, hmac

esperado = (
    "sha256=" + hmac.new(segredo.encode(), corpo_bruto, hashlib.sha256).hexdigest()
)
ok = hmac.compare_digest(esperado, cabecalho_recebido)
```

Para mandar mais eventos (`faixa` gera um POST por faixa, cuidado):
`webhook_events = lancamento_concluido,sessao_concluida,faixa`.

### ntfy

`--ntfy meu-topico` publica em `ntfy.sh`; `--ntfy https://ntfy.meuservidor/meu-topico`
usa o seu servidor. Token (se o tópico exigir login) em `ntfy_token` ou
`QOBUZ_DL_NTFY_TOKEN`. Sessão com falha sai com prioridade maior e a tag `warning`.
Por álbum: `ntfy_events = lancamento_concluido,sessao_concluida`.

## Reescanear o servidor de música (`--rescan`)

Roda **uma vez no fim** e **só se alguma faixa nova foi baixada** (sessão em que
tudo já existia não pede varredura). Cada servidor falha sozinho, sem afetar os outros.

```
qobuz-dl dl URL --rescan navidrome,plex
```

| Servidor | Chamada | Chaves |
|:--- |:--- |:--- |
| `navidrome` / `subsonic` (também Gonic e outros que falam Subsonic) | `GET /rest/startScan` com token+sal | `subsonic_url`, `subsonic_user`, `subsonic_password` |
| `plex` | `GET /library/sections/<id>/refresh` com `X-Plex-Token` | `plex_url`, `plex_token`, `plex_section` (um ou vários ids, com vírgula) |
| `jellyfin` | `POST /Library/Refresh` | `jellyfin_url`, `jellyfin_api_key` |

- Plex: para achar o número da biblioteca, abra `/library/sections` no seu servidor
  (com o token).
- **Roon** não está na lista: ele acompanha as pastas monitoradas sozinho, e não
  conheço uma chamada de reescaneamento que dê para confirmar.
- **Emby** também não entrou (não consegui conferir a API dele).
- Estas chamadas seguem a documentação de cada servidor, mas **ainda não foram
  testadas contra servidores de verdade**. Se um falhar, o aviso mostra o código
  HTTP (a URL com token nunca é impressa); conte o que apareceu.

## Configuração

Precedência: **flag > variável de ambiente > `config.ini`**. Variável de ambiente é
`QOBUZ_DL_` + o nome da chave em maiúsculas (`QOBUZ_DL_PLEX_TOKEN`). Prefira ela (ou
o arquivo `config.ini`, que fica com permissão 600) para tokens e senhas, e não a
linha de comando, que aparece na lista de processos. Um modelo comentado está em
[`config.example.ini`](config.example.ini).

| Chave no `config.ini` | Flag | Padrão |
|:--- |:--- |:--- |
| `events_file` | `--events` | desligado |
| `on_complete`, `on_error`, `on_finish` | `--on-complete`, `--on-error`, `--on-finish` | vazio |
| `hook_timeout` | | `60` (segundos, 1 a 600) |
| `hook_http_timeout` | | `10` (segundos, 1 a 600) |
| `webhook_url`, `webhook_secret`, `webhook_events` | `--webhook` | vazio |
| `ntfy`, `ntfy_token`, `ntfy_events` | `--ntfy` | vazio |
| `rescan` | `--rescan` | vazio |
| `subsonic_url`, `subsonic_user`, `subsonic_password` | | vazio |
| `plex_url`, `plex_token`, `plex_section` | | vazio |
| `jellyfin_url`, `jellyfin_api_key` | | vazio |

As chaves `ntfy`, `ntfy_token`, `webhook_url`, `webhook_secret`,
`subsonic_password`, `plex_token` e `jellyfin_api_key` são tratadas como
credenciais (o tópico do ntfy e a URL do webhook funcionam como senha): `qobuz-dl
config set` não as altera (mesma regra do e-mail e da senha) e `config show` as
exibe como `<redacted>`. Escreva-as no `config.ini` ou use variáveis de ambiente.

Docker: ponha as variáveis no `environment:` do `docker-compose.yml`. Lembre que a
imagem só tem o que o `Dockerfile` instalou: um `--on-complete` que chame `curl`,
`rsync` ou `jq` precisa que eles existam lá dentro.

## Limites e comportamento

- Hook nenhum pode quebrar ou travar um download: toda falha vira aviso, e todo
  comando e toda requisição têm prazo.
- Com **Ctrl+C**, só o evento `sessao_concluida` (com `interrompida: true`) é
  gravado; não há rede nem comando, para o programa sair na hora.
- Sessão sem lançamento nenhum processado não dispara `--on-finish`, `--rescan`,
  webhook nem ntfy de fim de sessão.
- `--on-complete` também dispara quando o álbum já estava todo no disco; use
  `QOBUZ_DL_FAIXAS_NOVAS` para ignorar esse caso.
