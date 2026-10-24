# Qobuz → Apple Notes: snapshots e pendências

Este complemento prepara snapshots/histórico a partir do JSON gerado pelo `qobuz-dl-ultra`. Ele não acessa nem altera diretamente o Apple Notes e não marca artistas como verificados.

## Atalho do iOS: atualizar a Nota com um toque

Uma vez por semana (ou quando quiser), o Atalho exporta a Nota, pergunta ao Qobuz quem você segue e acrescenta **só os artistas que ainda não estão na Nota**. A Nota é a fonte da verdade: o que já está nela (marcado ou não) nunca é repetido.

Ações do Atalho, em ordem:

1. **Encontrar Notas** (nome é o da sua checklist, limite 1) → **Obter Detalhes das Notas** (Corpo).
2. **a-Shell › Put File**: envia o texto da Nota para `qobuz-nota.txt` em `~/Documents`.
3. **a-Shell › Execute Command** (guarde o resultado em `Linhas`):
   ```sh
   python3 -m qobuz_dl.qobuz_notes_sync --nota ~/Documents/qobuz-nota.txt --imprimir linhas
   ```
4. **Se** `Linhas` contém `[QOBUZ_ID:` → **Acrescentar à Nota** o texto `Linhas`.
5. **a-Shell › Execute Command**: `cat ~/Documents/qobuz-dl/artist-sync/ultimo-resumo.txt` → **Mostrar Resultado** (ou Notificação).

O resumo (`ultimo-resumo.txt`) diz quantos artistas você segue, quantos são novos/removidos desde a última verificação, quantos serão adicionados à Nota e os nomes. Em caso de erro, ele começa com `ERRO:`.

Proteções:

- Se a exportação da Nota vier vazia ou sem nenhum `[QOBUZ_ID:...]`, o script recusa (evita colar a lista inteira de novo). Em Nota realmente nova, use `--permitir-nota-vazia`.
- Em `--imprimir linhas` o erro nunca vai para a saída padrão, então não é acrescentado à Nota por engano.
- Se o Atalho falhar depois do script, nada se perde: na próxima execução os mesmos artistas voltam a aparecer, porque a lista é recalculada contra a Nota.
- O `artists --json` pode imprimir banners de login; o script acha o JSON no meio da saída.

Artista removido da Nota por você volta na próxima execução enquanto você ainda o seguir no Qobuz.

## 1. Gerar a lista atual no a-Shell

```sh
python3 -m qobuz_dl artists --json > ~/Documents/qobuz-artists.json
```

Confirme que o JSON é válido antes de continuar:

```sh
python3 -m json.tool ~/Documents/qobuz-artists.json >/dev/null && echo JSON_OK
```

## 2. Comparar e gravar arquivos

Salve `qobuz_notes_sync.py` em uma pasta que o a-Shell consiga acessar e execute:

```sh
python3 ~/Documents/qobuz_notes_sync.py ~/Documents/qobuz-artists.json --dir ~/Documents/qobuz-dl/artist-sync
```

Se o script estiver em outra pasta, ajuste somente o caminho do script.

## 3. Arquivos produzidos

- `artists-current.json`: snapshot atual.
- `artists-previous.json`: snapshot anterior, quando já existia um snapshot.
- `artists-new.json`: artistas que apareceram nesta execução.
- `artists-removed.json`: artistas que deixaram de ser seguidos.
- `artists-pending.json` e `artists-pending.txt`: união acumulada dos artistas novos, para o Atalho filtrar contra a Nota.
- `history/snapshot-DATA.json`: histórico de cada verificação.

Na primeira execução, como não há snapshot anterior, todos os artistas serão classificados como novos e entrarão na pendência. Isso é intencional para criar a checklist inicial.

## Limites importantes

- O script se recusa a atualizar os snapshots se o JSON estiver inválido ou a lista vier vazia.
- O Atalho deve ler a Nota e comparar `[QOBUZ_ID:...]` com os IDs de `artists-pending.json` ou `artists-pending.txt`; exiba somente IDs ausentes na Nota.
- Depois de adicionar artistas à Nota, a próxima execução filtrará esses IDs porque já estarão presentes nela. A lista pendente é cumulativa e não é apagada automaticamente.
- Não use `--espelhar` na sincronização da checklist até validar o conteúdo exportado da Nota e a correspondência dos IDs.
