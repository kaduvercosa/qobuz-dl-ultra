# Qobuz-DL Studio — trabalho concluído

## Recursos e correções entregues

- [x] Aplicação web local, com servidor preso ao loopback por padrão.
- [x] Identidade visual própria em alto contraste, glifos pontilhados e vermelho de acento inspirada em interfaces industriais monocromáticas.
- [x] Busca no catálogo, navegação de álbuns/faixas, favoritos, biblioteca local e player browser para fluxos diretos compatíveis.
- [x] Fila integrada ao downloader Qobuz-DL já existente.
- [x] Configuração inicial pela GUI: valida e-mail/token antes de salvar, oferece Keyring do sistema ou config local restrito e não retorna o token pela API.
- [x] Preferências persistentes em `gui.json`: diretório, qualidade, capas, letras/LRC, créditos, M3U, fallback, playlist por álbum, validação de downloads, formatos, tamanhos de artwork, tags e paralelismo.
- [x] Painel Ferramentas para diagnóstico, estatísticas, catálogo, scan, sincronização, letras, inspeção, duplicatas, playlists, downloads, conta e manutenção.
- [x] Execução de comandos allowlistada, sem shell arbitrário, com processos monitoráveis e canceláveis.
- [x] Confirmações para operações com downloads, mutações e exclusão do banco; modo de simulação padrão onde suportado.
- [x] Estado de conta compatível com token no Keyring; correção do código de saída do comando purge.

## Validação final

- [x] Suíte: **1.369 testes aprovados, 3 ignorados, 31 excluídos**; cobertura **64,53%** (limite configurado: 60%).
- [x] Lint Ruff, compilação Python, sintaxe JavaScript e `git diff --check` aprovados.
- [x] Wheel construída e conferida: módulos, HTML/CSS/JS e assets visuais incluídos.
- [x] Prévia revisada no navegador; o modo demonstrativo não permite login nem execução de comandos reais.
- [x] ZIP fonte criado e validado sem erros de integridade.

## Limites conhecidos

- O player browser atende formatos diretos compatíveis; streams Hi-Res com decodificação proprietária devem ser baixados.
- O browser não consegue escolher caminhos locais por picker de arquivo; informe o diretório local em Preferências.
- A instalação do extra GUI acontece uma vez pelo instalador Python; depois, `qobuz-dl-studio` inicia a GUI local.
