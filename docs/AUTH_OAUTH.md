# Autenticação OAuth do Qobuz

O Qobuz-DL-Ultra mantém o fluxo manual/Web Player existente e oferece uma
segunda modalidade de autenticação por OAuth. Os dois modos usam pares de
credenciais de aplicação diferentes; não copie o `APP_ID` de um modo para o
outro nem combine o segredo do Web Player com o token OAuth.

O OAuth também funciona no primeiro acesso, mesmo quando ainda não existe
`config.ini`. No primeiro acesso ou ao executar `qobuz-dl -r`, o assistente
tenta OAuth antes de solicitar credenciais manuais. Depois do login, ele
continua com as perguntas originais sobre letras, traduções, Genius, pasta,
formato e qualidade. Se o OAuth falhar ou for cancelado, o assistente oferece
o login manual pelo Web Player.

No comando explícito `qobuz-dl auth --oauth`, a configuração só é gravada
depois da validação bem-sucedida das credenciais.

## Login pelo navegador

```bash
qobuz-dl auth --oauth
```

O programa tenta abrir a página oficial do Qobuz e aguarda o retorno local em
`localhost:11111`. Quando o ambiente não consegue abrir um navegador ou não
permite o callback, ele solicita a URL de retorno para colagem manual.

## Login em EC2/Linux sem interface gráfica ou via iPad/a-Shell

```bash
qobuz-dl auth --oauth-no-browser
```

1. Abra o endereço apresentado pelo programa em um navegador.
2. Entre na sua conta Qobuz e autorize a aplicação.
3. Depois do redirecionamento, copie a URL completa da barra de endereço.
4. Cole a URL no terminal onde o Qobuz-DL-Ultra está aguardando.

Nesse modo, o navegador não precisa conseguir alcançar o EC2. O programa
extrai o código de autorização da URL colada e faz a troca de código pelo
terminal/servidor onde está sendo executado.

**Se o navegador já estiver logado no Qobuz**, a página só mostra "Conexão à
Qobuz realizada com sucesso" e a barra de endereço não ganha a URL com
`code_autorisation`: não há o que copiar. Abra o endereço numa **aba anônima
(privada)**, sem sessão. Depois do login, o navegador tenta abrir
`localhost:11111/callback?code_autorisation=...`; a página mostra erro de
conexão, e isso é o esperado. Copie essa URL da barra de endereço (pode estar
sem o `http://`) e cole no terminal. O código é de uso único e expira rápido:
cole logo e não o compartilhe.

No iPad/a-Shell, `qobuz-dl auth --oauth` (sem `--oauth-no-browser`) também
funciona: o a-Shell recebe o retorno local e não é preciso colar nada.

## Alternativa manual existente

O fluxo de token do Web Player continua disponível:

```bash
qobuz-dl auth -l
```

Ao trocar de OAuth para o token manual do Web Player, o programa atualiza o
`APP_ID` e os segredos do bundle para manter o par compatível.

## Segurança e comportamento

- O token só é salvo depois da troca OAuth e da validação pelo cliente do
  Qobuz-DL-Ultra.
- O arquivo `config.ini` continua sendo gravado atomicamente com permissões
  restritas; o Keyring é usado quando disponível.
- Códigos de autorização e tokens não são impressos nos logs.
- Uma falha antes da validação preserva as credenciais anteriores.
- O Qobuz não fornece um refresh token reutilizável nesse fluxo. Quando o
  cliente identifica que o token foi rejeitado durante a inicialização de um
  comando que exige login, o programa inicia novamente o OAuth e tenta a
  inicialização uma única vez. Essa recuperação não é acionada por falhas de
  rede nem por assinatura inativa.
- O fluxo manual continua disponível em `qobuz-dl auth -l`; ele não é
  substituído pelo OAuth.

A troca OAuth e a validação de perfil são testadas com respostas HTTP simuladas.
A aceitação final do par de credenciais depende da compatibilidade atual dos
endpoints do Qobuz e deve ser confirmada com uma conta válida.
