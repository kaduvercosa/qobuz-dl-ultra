"""Lote 3 (parte pura): progresso do `lyrics` e registro para desfazer."""

import pytest

from qobuz_dl import lyrics_journal
from qobuz_dl.lyrics_journal import LyricsJournal
from qobuz_dl.lyrics_progress import LyricsProgress

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ progresso
@pytest.fixture
def pasta(tmp_path):
    raiz = tmp_path / "musica"
    raiz.mkdir()
    arquivos = []
    for nome in ("a.flac", "b.flac", "c.flac"):
        caminho = raiz / nome
        caminho.write_bytes(b"x")
        arquivos.append(str(caminho))
    return raiz, arquivos, str(tmp_path / "cfg")


def test_resume_pula_o_feito_e_refaz_os_adiados(pasta):
    raiz, (a, b, c), cfg = pasta
    p1 = LyricsProgress.open(cfg, str(raiz))
    assert not p1.interrupted
    p1.record(a, "ATUALIZADO")
    p1.record(b, "ADIADO")
    p1.save()  # nunca chamou finish(): execução interrompida

    p2 = LyricsProgress.open(cfg, str(raiz))
    assert p2.interrupted and p2.pending_count() == 1
    assert p2.status_of(a) == "ATUALIZADO"
    assert p2.filter_pending([a, b, c]) == ([a, b, c], 0)  # sem skip: tudo
    p2.enable_skip()
    assert p2.filter_pending([a, b, c]) == ([b, c], 1)


def test_arquivo_alterado_depois_volta_a_ser_processado(pasta):
    raiz, (a, b, c), cfg = pasta
    p = LyricsProgress.open(cfg, str(raiz))
    p.record(a, "SEM ALTERAÇÃO")
    p.enable_skip()
    assert p.filter_pending([a]) == ([], 1)
    (raiz / "a.flac").write_bytes(b"xx")  # trocado
    assert p.filter_pending([a]) == ([a], 0)


def test_falha_e_adiado_sempre_valem_nova_tentativa(pasta):
    raiz, (a, b, c), cfg = pasta
    p = LyricsProgress.open(cfg, str(raiz))
    p.record(a, "FALHA")
    p.record(b, "ADIADO")
    p.record(c, "NÃO ENCONTRADO")
    p.enable_skip()
    assert p.filter_pending([a, b, c]) == ([a, b], 1)


def test_finish_encerra_o_estado_de_interrompido(pasta):
    raiz, (a, *_), cfg = pasta
    p = LyricsProgress.open(cfg, str(raiz))
    p.record(a, "ATUALIZADO")
    p.finish()
    assert not LyricsProgress.open(cfg, str(raiz)).interrupted


def test_cada_pasta_tem_o_seu_progresso(pasta, tmp_path):
    raiz, (a, *_), cfg = pasta
    LyricsProgress.open(cfg, str(raiz)).record(a, "ATUALIZADO")
    outra = tmp_path / "outra"
    outra.mkdir()
    assert LyricsProgress.open(cfg, str(outra)).status_of(str(outra / "a.flac")) is None


def test_arquivo_de_progresso_corrompido_nao_derruba(pasta):
    raiz, (a, *_), cfg = pasta
    p = LyricsProgress.open(cfg, str(raiz))
    p.record(a, "ATUALIZADO")
    p.save()
    with open(p.path, "w", encoding="utf-8") as handle:
        handle.write("{ isto nao e json")
    assert LyricsProgress.open(cfg, str(raiz)).status_of(a) is None


# -------------------------------------------------------------------- desfazer
def _flac(caminho, corpo: bytes):
    """FLAC mínimo: 'fLaC' + 1 bloco de metadados (último) + 'áudio'."""
    cabeca = b"fLaC" + bytes([0x80]) + len(corpo).to_bytes(3, "big") + corpo
    caminho.write_bytes(cabeca + b"AUDIO" * 20)
    return cabeca


def test_desfaz_a_letra_embutida_e_os_lrc(tmp_path):
    cfg = tmp_path / "cfg"
    audio = tmp_path / "a.flac"
    novo = tmp_path / "b.flac"
    cabeca_original = _flac(audio, b"A" * 34)
    _flac(novo, b"C" * 34)
    (tmp_path / "a.lrc").write_text("antigo", encoding="utf-8")

    diario = LyricsJournal.create(str(cfg), str(tmp_path))
    snap_a = diario.snapshot(str(audio))
    snap_b = diario.snapshot(str(novo))

    # "o lyrics grava": a.flac ganha outra cabeça, a.lrc é reescrito, b.lrc nasce
    _flac(audio, b"B" * 35)
    (tmp_path / "a.lrc").write_text("novo", encoding="utf-8")
    (tmp_path / "b.lrc").write_text("criado", encoding="utf-8")
    assert diario.record(str(audio), snap_a) is True
    assert diario.record(str(novo), snap_b) is True  # só o .lrc mudou
    assert diario.recorded == 3

    resultado = lyrics_journal.undo(str(cfg))
    assert resultado["restored"] == 1 and resultado["lrc_restored"] == 2
    assert (tmp_path / "a.lrc").read_text(encoding="utf-8") == "antigo"
    assert not (tmp_path / "b.lrc").exists()
    conteudo = audio.read_bytes()
    assert conteudo.startswith(cabeca_original)
    assert conteudo.endswith(b"AUDIO" * 20)


def test_nao_restaura_o_que_mudou_depois_a_menos_que_force(tmp_path):
    cfg = tmp_path / "cfg"
    audio = tmp_path / "a.flac"
    _flac(audio, b"A" * 34)
    (tmp_path / "a.lrc").write_text("antigo", encoding="utf-8")
    diario = LyricsJournal.create(str(cfg), str(tmp_path))
    snap = diario.snapshot(str(audio))
    (tmp_path / "a.lrc").write_text("do lyrics", encoding="utf-8")
    diario.record(str(audio), snap)
    (tmp_path / "a.lrc").write_text("editado a mao", encoding="utf-8")

    res = lyrics_journal.undo(str(cfg))
    assert res["lrc_changed_since"] == 1 and res["lrc_restored"] == 0
    assert (tmp_path / "a.lrc").read_text(encoding="utf-8") == "editado a mao"

    res = lyrics_journal.undo(str(cfg), force=True)
    assert res["lrc_restored"] == 1
    assert (tmp_path / "a.lrc").read_text(encoding="utf-8") == "antigo"


def test_simulacao_do_desfazer_nao_grava_nada(tmp_path):
    cfg = tmp_path / "cfg"
    audio = tmp_path / "a.flac"
    _flac(audio, b"A" * 34)
    diario = LyricsJournal.create(str(cfg), str(tmp_path))
    snap = diario.snapshot(str(audio))
    (tmp_path / "a.lrc").write_text("criado", encoding="utf-8")
    diario.record(str(audio), snap)

    vistos = []
    res = lyrics_journal.undo(
        str(cfg), dry_run=True, on_file=lambda s, p: vistos.append(s)
    )
    assert res["lrc_would_restore"] == 1 and res["lrc_restored"] == 0
    assert vistos == ["would_restore"]
    assert (tmp_path / "a.lrc").exists()


def test_listagem_mostra_audio_e_lrc(tmp_path):
    cfg = tmp_path / "cfg"
    audio = tmp_path / "a.flac"
    _flac(audio, b"A" * 34)
    assert lyrics_journal.list_runs(str(cfg)) == []
    diario = LyricsJournal.create(str(cfg), str(tmp_path))
    snap = diario.snapshot(str(audio))
    (tmp_path / "a.lrc").write_text("criado", encoding="utf-8")
    diario.record(str(audio), snap)
    runs = lyrics_journal.list_runs(str(cfg))
    assert len(runs) == 1 and runs[0]["lrc"] == 1 and runs[0]["mode"] == "lyrics"
    assert runs[0]["id"] == diario.run_id


def test_sem_execucao_guardada_devolve_none(tmp_path):
    assert lyrics_journal.undo(str(tmp_path / "cfg")) is None
