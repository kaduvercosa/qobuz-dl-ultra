"""Trava a 'Pontuacao Inteligente' do iOS nos arquivos de documentacao.

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
Ao editar README.md ou SKILL.md pelo iPad, o teclado troca ``--`` por ``—``
(travessao) e aspas retas por curvas. Em texto corrido isso e' inofensivo, mas
dentro de comandos quebra o copiar e colar (``docker compose run —rm ...``) e,
nas linhas de ``---``, quebra o front-matter e as reguas do Markdown. Isso ja
aconteceu em 2026-10-13 e voltou em 2026-10-18.

O teste so' olha onde o estrago e' certo: reguas formadas so' por ``—``,
codigo (bloco ``` e `inline`) com travessao colado numa letra, e aspas curvas
dentro de codigo.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "SKILL.md"]

_FLAG_QUEBRADA = re.compile(r"(?<![\w—])—(?=[A-Za-z])")
_ASPAS_CURVAS = re.compile("[“”‘’]")
_INLINE = re.compile(r"`[^`\n]+`")


def _problemas(texto):
    achados, em_bloco = [], False
    for n, linha in enumerate(texto.split("\n"), 1):
        if linha.strip() == "—":
            achados.append((n, "linha so' com '—' (devia ser '---')", linha))
            continue
        if linha.lstrip().startswith("```"):
            em_bloco = not em_bloco
            continue
        trechos = [linha] if em_bloco else _INLINE.findall(linha)
        for trecho in trechos:
            if _FLAG_QUEBRADA.search(trecho):
                achados.append((n, "'—' no lugar de '--' dentro de codigo", linha))
            if _ASPAS_CURVAS.search(trecho):
                achados.append((n, "aspas curvas dentro de codigo", linha))
    return achados


@pytest.mark.regression
@pytest.mark.parametrize("nome", DOCS)
def test_doc_sem_pontuacao_inteligente(nome):
    arquivo = RAIZ / nome
    if not arquivo.is_file():
        pytest.skip(f"{nome} nao existe nesta instalacao")
    achados = _problemas(arquivo.read_text(encoding="utf-8"))
    detalhe = "\n".join(
        f"  {nome}:{n}: {motivo}: {l[:90]}" for n, motivo, l in achados[:10]
    )
    assert not achados, (
        "Pontuacao Inteligente do iOS alterou o arquivo. Troque '—' por '--' e "
        "aspas curvas por retas dentro do codigo (e '—' sozinho por '---'):\n" + detalhe
    )


def test_detector_pega_o_problema_e_poupa_a_prosa():
    ruim = "—\nrode `docker compose run —rm app` e `pytest -m “x”`\n"
    bom = "Texto — com travessao e “aspas” de prosa.\n```\ndocker run --rm app\n```\n---\n"
    assert len(_problemas(ruim)) == 3
    assert _problemas(bom) == []
