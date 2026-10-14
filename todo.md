# qobuz-dl-ultra — pendências

> Mantido junto com o `SKILL.md` (ver §11 de lá). A GUI web foi **removida** do projeto em 2026-10-06; este arquivo antes descrevia a GUI e foi reescrito.

## Para decidir / resolver

- [ ] **Cobertura mínima:** `pyproject.toml` (`fail_under = 75`) e `codecov.yml` (alvo 75%) pedem 75%, mas o CI (`tests.yml`) roda com `--cov-fail-under=60`. Medir a cobertura atual e alinhar os três no mesmo valor.
- [ ] **Baseline do mypy:** o `mypy-baseline.txt` não está versionado; enquanto não existir, o CI gera um novo a cada execução (então não detecta erro novo). Gerar uma vez e fazer commit.
- [ ] **Quebra de arquivos grandes:** `core.py`, `downloader.py` e `utils.py` (~2–3 mil linhas cada). Seguir extraindo só o que já tem teste (`download_utils.py`, `interactive_ui.py`, `stats_view.py` já foram).
- [ ] **README:** a linha de `usage` dos comandos globais está desatualizada (faltam `tags`, `inspect`, `import-playlist`, `sync-favorites`, `scan`, `library`, `doctor`, `auth`, `user`).

## Concluído recentemente

- [x] README: `—` e aspas curvas dentro de comandos trocados por `--` e aspas retas (copiar e colar voltou a funcionar).
- [x] Removido o `qobuz_dl/pyproject.toml` duplicado (cópia antiga, divergia em `Pillow`).
- [x] `tests/unit/test_core_interactive_branches-2026-09-28.py` renomeado para `test_core_interactive_branches.py` (o arquivo com esse nome estava vazio).
