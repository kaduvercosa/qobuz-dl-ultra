"""Testes do qobuz_notes_sync (Nota do iOS x artistas seguidos). Sem rede."""

import contextlib
import io
import json
import sys
import tempfile
import types
from pathlib import Path

from qobuz_dl import qobuz_notes_sync as sync

ARTISTS = [
    {"id": "1", "name": "Beyoncé", "url": "https://open.qobuz.com/artist/1"},
    {"id": "2", "name": "Adele", "url": "https://open.qobuz.com/artist/2"},
    {"id": "3", "name": "Zé Ramalho"},
]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = sync.main(argv)
    return code, out.getvalue(), err.getvalue()


def _setup(tmp: str, artists=ARTISTS, banner=True):
    base = Path(tmp)
    raw = json.dumps({"artists": artists}, ensure_ascii=False)
    (base / "artists.json").write_text(
        ("Logando... OK\n" if banner else "") + raw + "\n", encoding="utf-8"
    )
    return base


def test_extract_payload_ignora_banners_e_rejeita_lixo():
    text = 'Logado: OK\nAssinatura: Hi-Res\n{"artists":[{"id":"1","name":"A"}]}\nfim'
    assert sync.extract_payload(text)["artists"][0]["id"] == "1"
    assert sync.extract_payload("só texto") is None
    assert sync.extract_payload('{"ok": false, "error": "x"}') is None


def test_ids_na_nota_aceita_marcados_e_nao_marcados():
    note = "- [x] A - [QOBUZ_ID:10] - u\n- [ ] B - [QOBUZ_ID: 20 ] - u\nlinha solta"
    assert sync.ids_in_note(note) == {"10", "20"}


def test_com_nota_so_adiciona_o_que_falta_em_ordem_alfabetica():
    with tempfile.TemporaryDirectory() as tmp:
        base = _setup(tmp)
        (base / "nota.txt").write_text(
            "- [x] Adele - [QOBUZ_ID:2] - u\n", encoding="utf-8"
        )
        code, out, _ = _run(
            [
                str(base / "artists.json"),
                "--dir",
                str(base / "o"),
                "--nota",
                str(base / "nota.txt"),
                "--imprimir",
                "linhas",
            ]
        )
        assert code == 0
        lines = out.splitlines()
        assert [ln.split(" - ")[0] for ln in lines] == [
            "- [ ] Beyoncé",
            "- [ ] Zé Ramalho",
        ]
        assert "[QOBUZ_ID:3]" in lines[1] and lines[1].endswith("/artist/3")
        assert "Adele" not in out


def test_segunda_execucao_com_nota_atualizada_nao_repete_e_resume_novo():
    with tempfile.TemporaryDirectory() as tmp:
        base = _setup(tmp)
        nota = base / "nota.txt"
        nota.write_text("- [ ] Adele - [QOBUZ_ID:2] - u\n", encoding="utf-8")
        args = [
            str(base / "artists.json"),
            "--dir",
            str(base / "o"),
            "--nota",
            str(nota),
        ]
        _run(args)
        nota.write_text(
            nota.read_text()
            + "- [ ] Beyoncé - [QOBUZ_ID:1] - u\n- [ ] Zé - [QOBUZ_ID:3] - u\n"
        )
        novos = ARTISTS + [{"id": "4", "name": "Novo Artista"}]
        (base / "artists.json").write_text(
            json.dumps({"artists": novos}), encoding="utf-8"
        )
        code, out, _ = _run(args + ["--imprimir", "linhas"])
        assert code == 0
        assert out.splitlines() == [
            "- [ ] Novo Artista - [QOBUZ_ID:4] - https://open.qobuz.com/artist/4"
        ]
        resumo = (base / "o" / "ultimo-resumo.txt").read_text(encoding="utf-8")
        assert "Novos: 1" in resumo and "Novo Artista" in resumo
        pend = json.loads((base / "o" / "artists-pending.json").read_text())
        assert [a["id"] for a in pend["artists"]] == ["4"]


def test_nota_sem_ids_e_recusada_e_nada_vai_para_stdout():
    with tempfile.TemporaryDirectory() as tmp:
        base = _setup(tmp)
        (base / "nota.txt").write_text("", encoding="utf-8")
        code, out, err = _run(
            [
                str(base / "artists.json"),
                "--dir",
                str(base / "o"),
                "--nota",
                str(base / "nota.txt"),
                "--imprimir",
                "linhas",
            ]
        )
        assert code == 1 and out == "" and "ERRO" in err
        assert (
            (base / "o" / "ultimo-resumo.txt")
            .read_text(encoding="utf-8")
            .startswith("ERRO")
        )
        assert not (base / "o" / "artists-current.json").exists()
        code, out, _ = _run(
            [
                str(base / "artists.json"),
                "--dir",
                str(base / "o"),
                "--nota",
                str(base / "nota.txt"),
                "--imprimir",
                "linhas",
                "--permitir-nota-vazia",
            ]
        )
        assert code == 0 and len(out.splitlines()) == 3


def test_lista_vazia_nao_sobrescreve_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        base = _setup(tmp)
        assert _run([str(base / "artists.json"), "--dir", str(base / "o")])[0] == 0
        antes = (base / "o" / "artists-current.json").read_text()
        (base / "artists.json").write_text('{"artists": []}', encoding="utf-8")
        assert _run([str(base / "artists.json"), "--dir", str(base / "o")])[0] == 1
        assert (base / "o" / "artists-current.json").read_text() == antes


def test_sem_nota_mantem_comportamento_antigo_de_pendencia_acumulada():
    with tempfile.TemporaryDirectory() as tmp:
        base = _setup(tmp)
        code, out, _ = _run([str(base / "artists.json"), "--dir", str(base / "o")])
        assert code == 0
        data = json.loads(out)
        assert data["ok"] and data["new_this_check"] == 3 and data["pending_total"] == 3


def test_busca_em_processo_ignora_banner_do_cli():
    fake = types.ModuleType("qobuz_dl.cli")

    def main():
        print("Logando...")
        print(json.dumps({"artists": [{"id": "9", "name": "X"}]}))

    fake.main = main
    import qobuz_dl

    old = sys.modules.get("qobuz_dl.cli")
    sys.modules["qobuz_dl.cli"] = fake
    qobuz_dl.cli = fake
    try:
        assert sync.fetch_payload()["artists"][0]["id"] == "9"
    finally:
        if old is not None:
            sys.modules["qobuz_dl.cli"] = old
            qobuz_dl.cli = old
        else:
            sys.modules.pop("qobuz_dl.cli", None)
            del qobuz_dl.cli
