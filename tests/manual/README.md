# Verificações manuais

Os scripts desta pasta **não** rodam no `pytest`. Eles precisam de credenciais
reais do Qobuz e de acesso à internet, então servem para conferência manual, não
para a suíte automatizada.

## Por que ficaram separados

`verificar_letras.py` estava em `tests/` com o nome `test_lyrics.py`. O prefixo
`test_` faz o `pytest` tentar coletá-lo, e o arquivo:

- exige login real no Qobuz (sem credenciais, falha na autenticação);
- faz requisições de rede reais (lento e instável);
- imprime resultado para leitura humana em vez de afirmar (`assert`) qualquer coisa.

Ou seja: parecia teste automatizado, mas não era. Um arquivo assim na suíte só
produz falha vermelha que todo mundo aprende a ignorar — e uma suíte que se
ignora não protege nada.

## Como rodar

```bash
# precisa de config.ini com credenciais válidas
python -m tests.manual.verificar_letras
```

## A suíte automatizada

```bash
pip install -e ".[dev]"
pytest                       # tudo
pytest -m "not slow"         # pula os que abrem subprocesso
pytest tests/test_rede.py -v # só a camada de rede (offline, via pytest-httpx)
```
