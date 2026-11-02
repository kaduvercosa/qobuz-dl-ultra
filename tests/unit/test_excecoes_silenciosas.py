"""Teste-guarda: nenhum ``except`` pode engolir o erro sem dizer por quê.

Um handler cujo corpo é só ``pass`` ou ``continue`` esconde falhas reais (foi
assim que um aviso perdido só apareceu na suíte inteira). Quando o silêncio é
de propósito -- limpeza best-effort, entrada inválida, recurso opcional --,
escreva o motivo num comentário dentro do handler. Quando não é, registre com
``logger.debug(..., exc_info=True)`` (ou ``warning`` se o usuário precisa saber).
"""

import ast
from pathlib import Path

PACOTE = Path(__file__).resolve().parents[2] / "qobuz_dl"

# Funções cujo silêncio já é o contrato documentado na própria docstring
# (best-effort: ignoram erro de leitura/remoção de arquivo e seguem para o
# próximo). Chave: (arquivo, função que contém o handler). Não use números de
# linha aqui -- eles mudam a cada edição do arquivo.
SILENCIO_DOCUMENTADO_NA_DOCSTRING = {
    # "Ignora capa, booklet, parciais e qualquer erro de leitura."
    ("download_utils.py", "audio_bytes"),
    # "Best-effort: nunca levanta erro."
    ("download_utils.py", "remove_missing_placeholder"),
}


def _tem_justificativa(linhas: list[str]) -> bool:
    for linha in linhas:
        if "#" not in linha:
            continue
        texto = linha.split("#", 1)[1].strip()
        # Uma diretiva de supressão sozinha silencia o linter, mas não explica nada.
        if texto and not texto.lower().startswith("noqa"):
            return True
    return False


def _handlers_com_funcao(no, funcao=None):
    """Gera (handler, função que o contém) para cada ``except`` da árvore."""
    if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcao = no.name
    if isinstance(no, ast.ExceptHandler):
        yield no, funcao
    for filho in ast.iter_child_nodes(no):
        yield from _handlers_com_funcao(filho, funcao)


def _handlers_silenciosos_sem_motivo() -> list[str]:
    achados = []
    for arquivo in sorted(PACOTE.glob("*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        linhas = fonte.splitlines()
        for no, funcao in _handlers_com_funcao(ast.parse(fonte)):
            if len(no.body) != 1 or not isinstance(
                no.body[0], (ast.Pass, ast.Continue)
            ):
                continue
            if (arquivo.name, funcao) in SILENCIO_DOCUMENTADO_NA_DOCSTRING:
                continue
            if not _tem_justificativa(linhas[no.lineno - 1 : no.end_lineno]):
                achados.append(f"{arquivo.name}:{no.lineno}")
    return achados


def test_nenhum_except_silencioso_sem_motivo():
    achados = _handlers_silenciosos_sem_motivo()
    assert not achados, (
        "except que só faz pass/continue sem comentário explicando o motivo "
        "(comente o porquê ou registre com logger.debug(..., exc_info=True)):\n  "
        + "\n  ".join(achados)
    )
