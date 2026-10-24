"""Nota recriada (sem itens) + --atualizar-seguidos: todos os seguidos voltam como novos."""

import asyncio
import tempfile
from pathlib import Path

from qobuz_dl import artist_review as ar
from qobuz_dl.library_db import LibraryDB

FOLLOWED = [
    {"source_artist_id": "1", "name": "Adele"},
    {"source_artist_id": "2", "name": "Beyoncé"},
    {"source_artist_id": "3", "name": "Alok"},
]


def _db(tmp):
    lib = LibraryDB(Path(tmp) / "library.db")
    lib.sync_followed_artists(ar.SOURCE, FOLLOWED)
    return lib


def test_cabecalho_sem_itens_so_vale_com_allow_empty():
    for text in ("", "# tracklist\n"):
        try:
            ar.parse_notes_checklist(text)
        except ValueError:
            pass
        else:
            raise AssertionError("deveria recusar sem allow_empty")
        assert ar.parse_notes_checklist(text, allow_empty=True) == []


def test_nota_vazia_devolve_todos_os_seguidos_como_novos():
    with tempfile.TemporaryDirectory() as tmp:
        lib = _db(tmp)
        text = "# tracklist\n"
        result = asyncio.run(ar.sync_notes_checklist(text, lib, allow_empty=True))
        assert result["ok"] and result["parsed"] == 0
        items = ar.parse_notes_checklist(text, allow_empty=True)
        missing = ar.compute_new_followed_missing(FOLLOWED, items)
        assert [a["name"] for a in missing] == ["Adele", "Alok", "Beyoncé"]
        assert (
            missing[0]["line"]
            == "- [ ] Adele - [QOBUZ_ID:1] - https://open.qobuz.com/artist/1"
        )


def test_linhas_novas_acrescentadas_ao_arquivo_entram_como_pendentes():
    with tempfile.TemporaryDirectory() as tmp:
        lib = _db(tmp)
        base = "- [x] Adele - [QOBUZ_ID:1] - https://open.qobuz.com/artist/1"
        novas = "\n".join(
            a["line"]
            for a in ar.compute_new_followed_missing(
                FOLLOWED, ar.parse_notes_checklist(base)
            )
        )
        result = asyncio.run(
            ar.sync_notes_checklist(f"# tracklist\n{base}\n{novas}\n", lib)
        )
        assert result["parsed"] == 3
        assert sorted(i["name"] for i in result["pending"]) == ["Alok", "Beyoncé"]
        assert [i["name"] for i in result["marked"]] == ["Adele"]
