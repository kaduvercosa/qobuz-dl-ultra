"""`inspect_data`: leitura detalhada de UM arquivo, com áudio de verdade.

FLAC, MP3, M4A (ALAC), Ogg Vorbis e Opus minúsculos são gerados pelo ffmpeg e
lidos pelo mutagen real. Pula sozinho se um dos dois não estiver disponível
(no CI o ffmpeg é instalado de propósito).
"""

import json
import os
import shutil
import subprocess

import pytest

from qobuz_dl import inspect_data, library_report

FFMPEG = shutil.which("ffmpeg")

try:  # o mutagen pode estar simulado/quebrado no ambiente
    from mutagen.flac import FLAC

    FLAC.__init__  # noqa: B018 -- só confirma que não é um MagicMock
    _MUTAGEN_REAL = isinstance(FLAC, type)
except Exception:  # noqa: BLE001
    _MUTAGEN_REAL = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not FFMPEG, reason="ffmpeg indisponível"),
    pytest.mark.skipif(not _MUTAGEN_REAL, reason="mutagen real indisponível"),
]


def _encoders_disponiveis():
    """Nomes dos encoders de áudio que o ffmpeg deste ambiente realmente tem."""
    if not FFMPEG:
        return set()
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return set()
    nomes = set()
    for linha in r.stdout.splitlines():
        partes = linha.split()
        if len(partes) >= 2 and partes[0].startswith("A"):
            nomes.add(partes[1])
    return nomes


_ENCODERS = _encoders_disponiveis()

# ext -> [(encoder necessário, argumentos do ffmpeg)]; vale o primeiro disponível.
# No a-Shell não existe libvorbis: só o encoder nativo (experimental) `vorbis`.
_OPCOES = {
    "flac": [("flac", ["-c:a", "flac"])],
    "mp3": [("libmp3lame", ["-c:a", "libmp3lame", "-b:a", "192k"])],
    "m4a": [("alac", ["-c:a", "alac"])],
    "ogg": [
        ("libvorbis", ["-c:a", "libvorbis"]),
        ("vorbis", ["-c:a", "vorbis", "-strict", "-2", "-ac", "2"]),
    ],
    "opus": [("libopus", ["-c:a", "libopus"])],
}  # fmt: skip

FORMATOS = {}
for _ext, _opcoes in _OPCOES.items():
    for _enc, _args in _opcoes:
        if _enc in _ENCODERS:
            FORMATOS[_ext] = _args
            break

# Formatos sem encoder neste ffmpeg viram SKIP (não ERROR).
EXTS = [
    pytest.param(
        _ext,
        marks=pytest.mark.skipif(
            _ext not in FORMATOS, reason=f"ffmpeg sem encoder para {_ext}"
        ),
    )
    for _ext in _OPCOES
]

TAGS = {
    "title": "Faixa Teste",
    "artist": "Dua Lipa",
    "album": "Album X",
    "album_artist": "Dua Lipa",
    "date": "2024",
    "track": "3/12",
    "disc": "1/2",
    "genre": "Pop",
}


def _ffmpeg(*args):
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", *args], check=True, capture_output=True
    )


def _metadados(extra=None):
    saida = []
    for chave, valor in {**TAGS, **(extra or {})}.items():
        saida += ["-metadata", f"{chave}={valor}"]
    return saida


@pytest.fixture(scope="module")
def audios(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("inspect_audios")
    capa = str(pasta / "capa.jpg")
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=600x600:d=1", "-frames:v", "1", capa)
    pequena = str(pasta / "pequena.jpg")
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x32:d=1", "-frames:v", "1", pequena)
    arquivos = {}
    for ext, codec in FORMATOS.items():
        destino = str(pasta / f"t.{ext}")
        _ffmpeg(
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", *codec,
            *_metadados({"isrc": "GBAHT2000123"}), destino,
        )  # fmt: skip
        arquivos[ext] = destino
    # FLAC completo: capa quadrada, IDs do Qobuz e letra sincronizada
    completo = str(pasta / "completo.flac")
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-i", capa,
        "-map", "0", "-map", "1", "-c:a", "flac", "-c:v", "mjpeg",
        "-disposition:v", "attached_pic",
        *_metadados({
            "isrc": "GBAHT2000123",
            "QOBUZ_TRACK_ID": "555",
            "QOBUZALBUMID": "0060075",
            "lyrics": "[00:01.00]primeira linha\n[00:02.00]segunda linha",
        }),
        completo,
    )  # fmt: skip
    (pasta / "completo.lrc").write_text("[00:01.00]a\n[00:02.00]b\n", encoding="utf-8")
    arquivos["completo"] = completo
    # MP3 gravado como o `tags` grava (TSRC e IDs em TXXX): para a paridade com o relatório
    projeto = str(pasta / "projeto.mp3")
    shutil.copy(arquivos["mp3"], projeto)
    import mutagen.id3 as id3

    marcas = id3.ID3(projeto)
    marcas.delall("TXXX")
    marcas.add(id3.TSRC(encoding=3, text="GBAHT2000123"))
    marcas.add(id3.TXXX(encoding=3, desc="QOBUZTRACKID", text="555"))
    marcas.save(projeto)
    arquivos["projeto_mp3"] = projeto
    # FLAC com capa pequena e não quadrada
    pequeno = str(pasta / "capa_pequena.flac")
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-i", pequena,
        "-map", "0", "-map", "1", "-c:a", "flac", "-c:v", "mjpeg",
        "-disposition:v", "attached_pic", *_metadados(), pequeno,
    )  # fmt: skip
    arquivos["capa_pequena"] = pequeno
    arquivos["pasta"] = str(pasta)
    return arquivos


# ---------------------------------------------------------------------------
# Tags iguais em todos os formatos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ext", EXTS)
def test_campos_iguais_em_todos_os_formatos(audios, ext):
    campos = inspect_data.ler_arquivo(audios[ext])["campos"]
    assert campos["titulo"] == "Faixa Teste"
    assert campos["artista"] == "Dua Lipa"
    assert campos["albumartista"] == "Dua Lipa"
    assert campos["album"] == "Album X"
    assert campos["data"] == "2024"
    assert campos["genero"] == "Pop"
    assert (campos["faixa"], campos["faixa_total"]) == ("3", "12")
    assert (campos["disco"], campos["disco_total"]) == ("1", "2")


@pytest.mark.parametrize("ext", EXTS)
def test_nenhuma_tag_se_perde_nos_grupos(audios, ext):
    dados = inspect_data.ler_arquivo(audios[ext])
    tags = dados["tags"]
    assert tags, "o arquivo de teste tem tags"
    mostradas = {rotulo for _, itens in dados["grupos"] for rotulo, _ in itens}
    outras = {rotulo for titulo, itens in dados["grupos"] if "OUTRAS" in titulo
              for rotulo, _ in itens}  # fmt: skip
    for chave in tags:
        canon = inspect_data._chave_canonica(chave)
        agrupada = canon in inspect_data._CAMPOS_AGRUPADOS
        assert agrupada or chave in outras, f"{chave} sumiu do relatório"
    # e o que foi para os grupos aparece com o rótulo em português
    assert {"Título", "Artista", "Álbum"} <= mostradas


def test_faixa_mostra_o_total(audios):
    grupos = dict(inspect_data.ler_arquivo(audios["flac"])["grupos"])
    assert ("Faixa", "3 de 12") in grupos["🔢 FAIXA E DISCO"]
    assert ("Disco", "1 de 2") in grupos["🔢 FAIXA E DISCO"]


def test_ids_do_qobuz_vao_para_o_grupo_proprio(audios):
    dados = inspect_data.ler_arquivo(audios["completo"])
    grupos = dict(dados["grupos"])
    assert ("ID da faixa", "555") in grupos["🆔 IDS DO QOBUZ"]
    assert ("ID do álbum", "0060075") in grupos["🆔 IDS DO QOBUZ"]
    assert not any(p["codigo"] in ("no_track_id", "no_album_id")
                   for p in dados["problemas"])  # fmt: skip


# ---------------------------------------------------------------------------
# Técnico
# ---------------------------------------------------------------------------
def test_tecnico_flac(audios):
    tec = inspect_data.ler_arquivo(audios["flac"])["tecnico"]
    assert tec["Codec"] == "FLAC" and tec["Compressão"].startswith("sem perdas")
    assert tec["Assinatura MD5 do áudio"] == "presente"
    assert tec["Qualidade"] == "16 bits / 44.1 kHz"
    assert tec["Bitrate real (arquivo)"].endswith("kbps")
    assert tec["Duração"] == "0:02"


def test_tecnico_mp3_e_com_perdas(audios):
    tec = inspect_data.ler_arquivo(audios["mp3"])["tecnico"]
    assert tec["Codec"] == "MP3" and tec["Compressão"].startswith("com perdas")
    assert tec["Qualidade"].endswith("kbps") and "MP3" not in tec["Qualidade"]


def test_tecnico_alac_e_sem_perdas(audios):
    tec = inspect_data.ler_arquivo(audios["m4a"])["tecnico"]
    assert tec["Codec"] == "ALAC" and tec["Compressão"].startswith("sem perdas")
    assert tec["Profundidade de bits"] == "16 bits"


# ---------------------------------------------------------------------------
# Capas, letras e diagnóstico
# ---------------------------------------------------------------------------
def test_capa_quadrada_e_grande_nao_gera_aviso(audios):
    dados = inspect_data.ler_arquivo(audios["completo"])
    (capa,) = dados["capas"]
    assert (capa["largura"], capa["altura"]) == (600, 600)
    assert capa["quadrada"] is True and capa["mime"] == "image/jpeg"
    codigos = {p["codigo"] for p in dados["problemas"]}
    assert not codigos & {"no_cover", "small_cover", "bad_shape_cover"}


def test_capa_pequena_e_retangular_geram_avisos(audios):
    dados = inspect_data.ler_arquivo(audios["capa_pequena"])
    codigos = {p["codigo"] for p in dados["problemas"]}
    assert {"small_cover", "bad_shape_cover"} <= codigos
    assert dados["capas"][0]["quadrada"] is False


def test_sem_capa_avisa(audios):
    dados = inspect_data.ler_arquivo(audios["mp3"])
    assert dados["capas"] == []
    assert "no_cover" in {p["codigo"] for p in dados["problemas"]}


def test_letra_sincronizada_embutida_e_lrc_ao_lado(audios):
    letras = inspect_data.ler_arquivo(audios["completo"])["letras"]
    assert letras["embutida"] and letras["sincronizada"] and letras["linhas"] == 2
    assert letras["previa"] == "primeira linha"
    assert letras["lrc"] == {
        "arquivo": "completo.lrc",
        "linhas": 2,
        "sincronizada": True,
    }


def test_sem_letra(audios):
    letras = inspect_data.ler_arquivo(audios["flac"])["letras"]
    assert letras["embutida"] is False and letras["lrc"] is None


@pytest.mark.parametrize("ext", ["flac", "projeto_mp3", "completo", "capa_pequena"])
def test_diagnostico_igual_ao_do_tags_report(audios, ext):
    """`inspect` e `tags --report` usam as MESMAS regras (não podem divergir)."""
    caminho = audios[ext]
    if not caminho.endswith((".flac", ".mp3")):
        pytest.skip("o relatório da biblioteca só lê FLAC/MP3")
    esperado = library_report.inspect_file(caminho)["issues"]
    obtido = [p["codigo"] for p in inspect_data.ler_arquivo(caminho)["problemas"]]
    assert sorted(obtido) == sorted(esperado)


def test_arquivo_ilegivel_levanta_erro(tmp_path):
    ruim = tmp_path / "ruim.flac"
    ruim.write_text("não é áudio")
    with pytest.raises(Exception):  # noqa: B017 -- a exceção original do mutagen
        inspect_data.ler_arquivo(str(ruim))


# ---------------------------------------------------------------------------
# JSON, resumo e listagem
# ---------------------------------------------------------------------------
def test_para_json_e_serializavel_e_sem_campos_internos(audios):
    saida = inspect_data.para_json(inspect_data.ler_arquivo(audios["completo"]))
    texto = json.dumps(saida, ensure_ascii=False)
    assert "_sample_rate" not in saida and "_duracao_s" not in saida
    assert json.loads(texto)["campos"]["titulo"] == "Faixa Teste"


def test_resumo_curto(audios):
    r = inspect_data.resumo_curto(inspect_data.ler_arquivo(audios["completo"]))
    assert r["titulo"] == "Faixa Teste" and r["capa"] == "600x600"
    assert r["codec"] == "FLAC" and r["problemas"] == []


def test_listar_audios_ordena_e_ignora_o_resto(tmp_path):
    (tmp_path / "b.mp3").write_bytes(b"x")
    (tmp_path / "a.FLAC").write_bytes(b"x")
    (tmp_path / "notas.txt").write_text("x")
    (tmp_path / ".oculto.flac").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.m4a").write_bytes(b"x")
    nomes = [
        os.path.relpath(p, tmp_path) for p in inspect_data.listar_audios(str(tmp_path))
    ]
    assert nomes == ["a.FLAC", "b.mp3", os.path.join("sub", "c.m4a")]


@pytest.mark.parametrize(
    "tamanho, esperado",
    [(0, "0 B"), (1536, "1.5 KB"), (5 * 1024**2, "5.0 MB"), (3 * 1024**4, "3.0 TB")],
)
def test_formatar_tamanho(tamanho, esperado):
    assert inspect_data.formatar_tamanho(tamanho) == esperado


@pytest.mark.parametrize(
    "segundos, esperado",
    [(0, "0:00"), (59.6, "1:00"), (225, "3:45"), (3725, "1:02:05"), (None, "?")],
)
def test_formatar_duracao(segundos, esperado):
    assert inspect_data.formatar_duracao(segundos) == esperado


# ---------------------------------------------------------------------------
# ffmpeg: integridade e volume
# ---------------------------------------------------------------------------
def test_integridade_de_arquivo_bom(audios):
    assert inspect_data.verificar_integridade(audios["flac"])["ok"] is True


def test_integridade_detecta_arquivo_que_nao_e_audio(tmp_path):
    ruim = tmp_path / "falso.flac"
    ruim.write_bytes(b"isto nao e audio" * 500)
    resultado = inspect_data.verificar_integridade(str(ruim))
    assert resultado["ok"] is False and resultado["mensagem"]


def test_integridade_repassa_o_erro_do_decodificador(audios, monkeypatch):
    monkeypatch.setattr(
        "qobuz_dl.utils.verify_audio_integrity",
        lambda caminho, timeout=180: (False, "Invalid data found"),
    )
    assert inspect_data.verificar_integridade(audios["flac"]) == {
        "ok": False,
        "mensagem": "Invalid data found",
    }


def test_integridade_sem_ffmpeg_nao_inventa_resultado(audios, monkeypatch):
    monkeypatch.setattr("qobuz_dl.utils.encontrar_binario", lambda nome: None)
    assert inspect_data.verificar_integridade(audios["flac"])["ok"] is None
    assert inspect_data.medir_volume(audios["flac"]) is None


def test_volume_real(audios):
    volume = inspect_data.medir_volume(audios["flac"])
    assert volume is not None
    assert -60 < volume["integrado_lufs"] < 0
    assert volume["pico_dbfs"] <= 0


def test_volume_interpreta_o_resumo_do_ffmpeg(monkeypatch, tmp_path):
    saida = (
        "[Parsed_ebur128_0 @ 0x1] t: 0.1 TARGET:-23 LUFS M: -20.0 I: -99.0 LUFS\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
        "    I:         -9.2 LUFS\n    Threshold: -19.3 LUFS\n\n  Loudness range:\n"
        "    LRA:         5.1 LU\n\n  True peak:\n    Peak:       -0.3 dBFS\n"
    )

    class Resultado:
        stderr = saida
        returncode = 0

    monkeypatch.setattr("qobuz_dl.utils.encontrar_binario", lambda nome: "/x/ffmpeg")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Resultado())
    assert inspect_data.medir_volume("qualquer.flac") == {
        "integrado_lufs": -9.2,
        "faixa_dinamica_lu": 5.1,
        "pico_dbfs": -0.3,
    }


def test_volume_de_arquivo_mudo_nao_quebra_o_json(monkeypatch):
    class Resultado:
        stderr = "Summary:\n I:  -inf LUFS\n LRA: 0.0 LU\n Peak: -inf dBFS\n"
        returncode = 0

    monkeypatch.setattr("qobuz_dl.utils.encontrar_binario", lambda nome: "/x/ffmpeg")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Resultado())
    volume = inspect_data.medir_volume("mudo.flac")
    assert volume["integrado_lufs"] is None and volume["pico_dbfs"] is None
    json.dumps(volume)  # tem de ser JSON válido (sem Infinity)


def test_completar_acrescenta_integridade_e_volume(audios):
    dados = inspect_data.completar(inspect_data.ler_arquivo(audios["flac"]))
    assert dados["integridade"]["ok"] is True and dados["volume"]
    assert "integridade" in inspect_data.para_json(dados)
