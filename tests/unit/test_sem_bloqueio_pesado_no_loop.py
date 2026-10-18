"""Guarda permanente: I/O pesado e síncrono não pode rodar direto no event loop.

Percorre (por AST) todas as funções `async def` de `qobuz_dl/` e falha se
alguma chamar *diretamente* algo que bloqueia por muito tempo: leitura de tags
(mutagen), `os.walk`, `time.sleep`, `subprocess`, cópias de arquivo, `sqlite3`,
HTTP síncrono -- ou um dos auxiliares do projeto que fazem isso.

A forma correta é `await asyncio.to_thread(funcao, args)` (a função entra como
argumento, não é chamada na hora) ou `loop.run_in_executor`.

Operações pequenas (`os.rename`, `os.remove`, `os.makedirs`, `input()` nos
assistentes interativos) ficam de fora de propósito.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.regression]

RAIZ = Path(__file__).resolve().parents[2] / "qobuz_dl"

ATRIBUTOS = {
    ("os", "walk"),
    ("time", "sleep"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("shutil", "copy"),
    ("shutil", "copy2"),
    ("shutil", "copyfile"),
    ("shutil", "copytree"),
    ("shutil", "move"),
    ("shutil", "rmtree"),
    ("sqlite3", "connect"),
    ("requests", "get"),
    ("requests", "post"),
}
NOMES = {
    # mutagen
    "FLAC", "ID3", "MP3", "MP4",
    # auxiliares do projeto que leem/varrem o disco ou usam HTTP síncrono
    "_scan_local_tracks", "_clean_empty_dirs", "find_audio_files",
    "lookup_saved_path", "_clean_embed_art", "_list_audio_files",
    "_read_local_tags", "_read_basic_tags", "_collect_audio_files",
    "_load_report", "_save_report", "extract_track_id",
    "inspect_existing_lyrics", "_lyrics_lookup_hints",
}  # fmt: skip

# (arquivo, função, chamada) -> motivo. Só entra aqui o que foi avaliado e aceito.
# `shutil.rmtree` dentro de `asyncio.to_thread` TRAVA no a-Shell/iOS (confirmado
# com um script isolado; `shutil.copyfile` e `shutil.copy2` em thread funcionam).
# Por isso a limpeza da pasta temporária do `tags --dry-run` fica síncrona.
PERMITIDOS = {
    ("retro_tags.py", "retag_directory", "shutil.rmtree"): "pasta temporária pequena",
}


def _chamadas_diretas(func):
    """Chamadas dentro da função, sem entrar em defs/lambdas aninhados."""
    pilha = list(ast.iter_child_nodes(func))
    while pilha:
        no = pilha.pop()
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(no, ast.Call):
            yield no
        pilha.extend(ast.iter_child_nodes(no))


def _nome(chamada):
    f = chamada.func
    if isinstance(f, ast.Name):
        return f.id if f.id in NOMES else None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if (f.value.id, f.attr) in ATRIBUTOS:
            return f"{f.value.id}.{f.attr}"
    return None


def _todas():
    """(arquivo, função, chamada, linha) de toda chamada pesada direta."""
    for arq in sorted(RAIZ.rglob("*.py")):
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.AsyncFunctionDef):
                for chamada in _chamadas_diretas(no):
                    nome = _nome(chamada)
                    if nome:
                        yield arq, no.name, nome, chamada.lineno


def _achados():
    for arq, funcao, nome, linha in _todas():
        if (arq.name, funcao, nome) not in PERMITIDOS:
            yield f"{arq.relative_to(RAIZ.parent)}:{linha} {funcao}() chama {nome}()"


def test_nenhuma_funcao_async_chama_io_pesado_direto():
    achados = list(_achados())
    assert not achados, (
        "I/O síncrono pesado dentro de `async def` (use asyncio.to_thread):\n  "
        + "\n  ".join(achados)
    )


def test_lista_de_permitidos_nao_ficou_velha():
    """Se uma exceção deixou de existir, ela sai da lista (senão vira buraco)."""
    existentes = {(a.name, f, n) for a, f, n, _ in _todas()}
    velhas = sorted(set(PERMITIDOS) - existentes)
    assert not velhas, f"remova de PERMITIDOS: {velhas}"


def test_nenhum_rmtree_vai_para_thread():
    """`asyncio.to_thread(shutil.rmtree, ...)` trava no a-Shell: não repita."""
    for arq in sorted(RAIZ.rglob("*.py")):
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Call) and getattr(no.func, "attr", "") in (
                "to_thread",
                "run_in_executor",
            ):
                for arg in no.args:
                    nome = ast.unparse(arg)
                    assert "rmtree" not in nome, f"{arq.name}:{no.lineno} {nome}"


def test_o_guarda_enxerga_o_problema(tmp_path):
    """Sanidade: o detector acusa uma chamada direta e aceita to_thread."""
    ruim = ast.parse("async def f():\n    FLAC('x')\n    time.sleep(1)\n")
    bom = ast.parse("async def f():\n    await asyncio.to_thread(FLAC, 'x')\n")
    achou = [_nome(c) for c in _chamadas_diretas(ruim.body[0])]
    assert sorted(a for a in achou if a) == ["FLAC", "time.sleep"]
    assert not [c for c in _chamadas_diretas(bom.body[0]) if _nome(c)]
