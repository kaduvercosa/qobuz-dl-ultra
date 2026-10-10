"""Testes de qobuz_dl/fileio.py (escrita atômica, checksums, limpeza).

Sem rede, sem subprocess: só disco temporário (tmp_path). Cada teste
documenta o PROBLEMA real que o módulo resolve -- a maioria vem de
travar o comportamento "o nome final só existe com conteúdo completo".
"""

import hashlib
import json
import os
import threading
import time
from unittest import mock

import pytest

from qobuz_dl import fileio

pytestmark = pytest.mark.unit


def _temporarios(pasta):
    return [n for n in os.listdir(pasta) if n.endswith(".atomic.tmp")]


# ---------------------------------------------------------------------------
# Escrita atômica
# ---------------------------------------------------------------------------


def test_json_roundtrip_com_acentos_e_newline_final(tmp_path):
    destino = tmp_path / "relatorio.json"
    fileio.atomic_write_json(destino, {"titulo": "Canção", "n": 1})

    assert json.loads(destino.read_text(encoding="utf-8")) == {
        "titulo": "Canção",
        "n": 1,
    }
    assert destino.read_bytes().endswith(b"\n")
    assert _temporarios(tmp_path) == []


def test_cria_pastas_intermediarias(tmp_path):
    destino = tmp_path / "a" / "b" / "c.txt"
    fileio.atomic_write_text(destino, "oi")
    assert destino.read_text() == "oi"


def test_excecao_no_meio_preserva_o_arquivo_anterior(tmp_path):
    destino = tmp_path / "cover.jpg"
    fileio.atomic_write_bytes(destino, b"ORIGINAL")

    with pytest.raises(RuntimeError):
        with fileio.atomic_writer(destino) as fh:
            fh.write(b"PARCIAL")
            raise RuntimeError("queda de rede")

    assert destino.read_bytes() == b"ORIGINAL"
    assert _temporarios(tmp_path) == []


def test_keyboard_interrupt_tambem_limpa_o_temporario(tmp_path):
    destino = tmp_path / "x.bin"
    fileio.atomic_write_bytes(destino, b"ORIGINAL")

    with pytest.raises(KeyboardInterrupt):
        with fileio.atomic_writer(destino) as fh:
            fh.write(b"PARCIAL")
            raise KeyboardInterrupt

    assert destino.read_bytes() == b"ORIGINAL"
    assert _temporarios(tmp_path) == []


def test_falha_antes_de_existir_nao_cria_o_destino(tmp_path):
    destino = tmp_path / "novo.bin"
    with pytest.raises(RuntimeError):
        with fileio.atomic_writer(destino) as fh:
            fh.write(b"PARCIAL")
            raise RuntimeError("boom")

    assert not destino.exists()
    assert _temporarios(tmp_path) == []


def test_json_nao_serializavel_nao_toca_o_arquivo(tmp_path):
    destino = tmp_path / "r.json"
    fileio.atomic_write_text(destino, "original")

    with pytest.raises(TypeError):
        fileio.atomic_write_json(destino, {"ruim": object()})

    assert destino.read_text() == "original"
    assert _temporarios(tmp_path) == []


def test_modo_texto_sempre_usa_lf(tmp_path):
    destino = tmp_path / "t.txt"
    fileio.atomic_write_text(destino, "a\nb\n")
    assert destino.read_bytes() == b"a\nb\n"


def test_modo_invalido_levanta_valueerror(tmp_path):
    with pytest.raises(ValueError):
        with fileio.atomic_writer(tmp_path / "x", "a"):
            pass


def test_escritas_concorrentes_nunca_deixam_json_incompleto(tmp_path):
    destino = tmp_path / "conc.json"

    def escritor(i):
        for k in range(25):
            fileio.atomic_write_json(
                destino, {"i": i, "k": k, "pad": "x" * 4000}, fsync=False
            )

    threads = [threading.Thread(target=escritor, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()

    leituras_invalidas = []
    while any(t.is_alive() for t in threads):
        if destino.exists():
            try:
                json.loads(destino.read_text(encoding="utf-8"))
            except ValueError as exc:  # JSON truncado seria detectado aqui
                leituras_invalidas.append(exc)
    for t in threads:
        t.join()

    assert leituras_invalidas == []
    assert _temporarios(tmp_path) == []


# ---------------------------------------------------------------------------
# replace_atomic / utilitários
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="retentativas só existem no Windows")
def test_replace_atomic_em_posix_propaga_permissionerror_sem_retentar(tmp_path):
    with mock.patch.object(
        fileio.os, "replace", side_effect=PermissionError("negado")
    ) as replace:
        with pytest.raises(PermissionError):
            fileio.replace_atomic(tmp_path / "a", tmp_path / "b")
    assert replace.call_count == 1


def test_part_path_e_safe_remove(tmp_path):
    assert fileio.part_path("pasta/faixa.flac") == "pasta/faixa.flac.part"

    arquivo = tmp_path / "x.part"
    arquivo.write_text("z")
    assert fileio.safe_remove(arquivo) is True
    assert fileio.safe_remove(arquivo) is False  # já não existe: sem erro


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------


def test_sha256_confere_com_hashlib_independente_do_tamanho_do_bloco(tmp_path):
    arquivo = tmp_path / "big.bin"
    dados = os.urandom(2 * 1024 * 1024 + 123)  # não múltiplo do bloco
    arquivo.write_bytes(dados)
    esperado = hashlib.sha256(dados).hexdigest()

    assert fileio.sha256_file(arquivo) == esperado
    assert fileio.sha256_file(arquivo, chunk_size=777) == esperado


def test_file_digest_aceita_outros_algoritmos(tmp_path):
    arquivo = tmp_path / "a.bin"
    arquivo.write_bytes(b"abc")
    assert fileio.file_digest(arquivo, "md5") == hashlib.md5(b"abc").hexdigest()


def test_verify_digest_casos(tmp_path):
    arquivo = tmp_path / "a.bin"
    arquivo.write_bytes(b"abc")
    certo = hashlib.sha256(b"abc").hexdigest()

    assert fileio.verify_digest(arquivo, certo) is True
    assert fileio.verify_digest(arquivo, certo.upper()) is True  # case-insensitive
    assert fileio.verify_digest(arquivo, "0" * 64) is False
    assert fileio.verify_digest(arquivo, "") is False
    assert fileio.verify_digest(tmp_path / "nao_existe", certo) is False


def test_verify_digest_detecta_arquivo_alterado(tmp_path):
    arquivo = tmp_path / "faixa.flac"
    arquivo.write_bytes(b"audio original")
    checksum = fileio.sha256_file(arquivo)

    arquivo.write_bytes(b"audio corrompido")
    assert fileio.verify_digest(arquivo, checksum) is False


# ---------------------------------------------------------------------------
# append_line
# ---------------------------------------------------------------------------


def test_append_line_cria_pasta_e_adiciona_newline(tmp_path):
    alvo = tmp_path / "sub" / "idx.jsonl"
    fileio.append_line(alvo, '{"a": 1}')
    fileio.append_line(alvo, '{"a": 2}\n')  # newline sobrando é normalizado

    assert alvo.read_text(encoding="utf-8").splitlines() == ['{"a": 1}', '{"a": 2}']


def test_append_line_concorrente_nao_mistura_linhas(tmp_path):
    alvo = tmp_path / "idx.jsonl"

    def escritor(i):
        for k in range(40):
            fileio.append_line(alvo, json.dumps({"i": i, "k": k, "pad": "y" * 300}))

    threads = [threading.Thread(target=escritor, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    linhas = alvo.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 6 * 40
    for linha in linhas:
        json.loads(linha)  # nenhuma linha pode estar cortada/misturada


# ---------------------------------------------------------------------------
# cleanup_stale_parts
# ---------------------------------------------------------------------------


def _montar_pasta_com_temporarios(base):
    """Cria temporários antigos (devem sair) e arquivos que devem ficar."""
    (base / "sub").mkdir()
    antigos = [
        "cover.jpg.part",
        "~tmp_01.tmp",
        "~tmp_01.tmp.mp4",
        ".1-ab.atomic.tmp",
        os.path.join("sub", "x.flac.part"),
    ]
    ficam = ["musica.flac", "notas.txt", "~tmp_novo.tmp"]

    velho = time.time() - 3 * 86400
    for nome in antigos + ficam:
        caminho = base / nome
        caminho.write_text("z")
        if nome in antigos:
            os.utime(caminho, (velho, velho))
    return antigos, ficam


def test_cleanup_dry_run_apenas_lista(tmp_path):
    antigos, _ = _montar_pasta_com_temporarios(tmp_path)

    achados = fileio.cleanup_stale_parts(tmp_path)

    assert sorted(os.path.relpath(p, tmp_path) for p in achados) == sorted(antigos)
    assert all((tmp_path / n).exists() for n in antigos)


def test_cleanup_apaga_so_os_temporarios_antigos(tmp_path):
    antigos, ficam = _montar_pasta_com_temporarios(tmp_path)

    removidos = fileio.cleanup_stale_parts(tmp_path, dry_run=False)

    assert len(removidos) == len(antigos)
    assert not any((tmp_path / n).exists() for n in antigos)
    # Áudio, texto e temporário RECENTE (download em andamento) são preservados.
    assert all((tmp_path / n).exists() for n in ficam)


def test_cleanup_nao_recursivo_ignora_subpastas(tmp_path):
    antigos, _ = _montar_pasta_com_temporarios(tmp_path)

    achados = fileio.cleanup_stale_parts(tmp_path, recursive=False)

    nomes = {os.path.relpath(p, tmp_path) for p in achados}
    assert os.path.join("sub", "x.flac.part") not in nomes
    assert "cover.jpg.part" in nomes


def test_cleanup_idade_zero_inclui_temporarios_recentes(tmp_path):
    _montar_pasta_com_temporarios(tmp_path)

    achados = fileio.cleanup_stale_parts(tmp_path, recursive=False, max_age_seconds=0)

    assert "~tmp_novo.tmp" in {os.path.basename(p) for p in achados}


@pytest.mark.skipif(os.name == "nt", reason="symlink exige privilégio no Windows")
def test_cleanup_ignora_links_simbolicos(tmp_path):
    alvo_real = tmp_path / "importante.flac"
    alvo_real.write_text("audio")
    link = tmp_path / "atalho.part"
    link.symlink_to(alvo_real)
    velho = time.time() - 3 * 86400
    os.utime(link, (velho, velho), follow_symlinks=False)

    achados = fileio.cleanup_stale_parts(tmp_path, dry_run=False)

    assert achados == []
    assert alvo_real.exists()
