"""`qobuz-dl inspect` de ponta a ponta: arquivo direto, --json, --resumo, --completo.

Usa áudio de verdade (ffmpeg + mutagen reais) e a saída real do `ui`. Pula
sozinho se algum dos dois não estiver disponível.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

from qobuz_dl import cli, inspector
from qobuz_dl.commands import qobuz_dl_args

FFMPEG = shutil.which("ffmpeg")

try:
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


def _ffmpeg(*args):
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", *args], check=True, capture_output=True
    )


@pytest.fixture(scope="module")
def biblioteca(tmp_path_factory):
    """Pasta com 2 FLAC (um completo, um sem capa/IDs), 1 MP3 e 1 arquivo ruim."""
    pasta = tmp_path_factory.mktemp("biblioteca")
    capa = str(pasta / "capa.jpg")
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=600x600:d=1", "-frames:v", "1", capa)
    base = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    completo = str(pasta / "01 completo.flac")
    _ffmpeg(
        *base, "-i", capa, "-map", "0", "-map", "1", "-c:a", "flac",
        "-c:v", "mjpeg", "-disposition:v", "attached_pic",
        "-metadata", "title=Completo", "-metadata", "artist=Dua Lipa",
        "-metadata", "album=Album X", "-metadata", "album_artist=Dua Lipa",
        "-metadata", "date=2024", "-metadata", "track=1/2",
        "-metadata", "isrc=GBAHT2000123", "-metadata", "QOBUZTRACKID=555",
        "-metadata", "QOBUZALBUMID=0060075", completo,
    )  # fmt: skip
    pobre = str(pasta / "02 pobre.flac")
    _ffmpeg(*base, "-c:a", "flac", "-metadata", "title=Pobre", pobre)
    mp3 = str(pasta / "03 mp3.mp3")
    _ffmpeg(*base, "-c:a", "libmp3lame", "-metadata", "title=Mp3", mp3)
    (pasta / "04 ruim.flac").write_text("não é áudio")
    return {"pasta": str(pasta), "completo": completo, "pobre": pobre, "mp3": mp3}


def _rodar(caminho, capsys, **kwargs):
    kwargs.setdefault("espectro", False)
    codigo = asyncio.run(inspector.run_inspector(caminho, **kwargs))
    return codigo, capsys.readouterr().out


@pytest.fixture(autouse=True)
def sem_navegador(monkeypatch):
    """O navegador de arquivos nunca pode abrir nestes testes (precisa de terminal)."""

    async def _nao_abre(*_a, **_k):
        raise AssertionError("o navegador de arquivos não deveria abrir")

    monkeypatch.setattr(inspector, "_navegar_arquivos", _nao_abre)


# ---------------------------------------------------------------------------
# Arquivo direto
# ---------------------------------------------------------------------------
def test_arquivo_direto_mostra_todas_as_secoes(biblioteca, capsys):
    codigo, saida = _rodar(biblioteca["completo"], capsys)
    assert codigo == 0
    for secao in (
        "📄 ARQUIVO",
        "🎼 IDENTIFICAÇÃO",
        "🔢 FAIXA E DISCO",
        "🏷️  CÓDIGOS E SELOS",
        "🆔 IDS DO QOBUZ",
        "🖼️  CAPAS EMBUTIDAS (1)",
        "🎤 LETRAS",
        "🩺 DIAGNÓSTICO",
        "🔬 AUTENTICIDADE",
    ):
        assert secao in saida, secao
    assert "Dua Lipa" in saida and "0060075" in saida and "600x600" in saida
    assert "Nada a corrigir" in saida
    assert "ignorada (--sem-espectro)" in saida


def test_arquivo_com_problemas_lista_cada_um_com_a_dica(biblioteca, capsys):
    _, saida = _rodar(biblioteca["pobre"], capsys)
    assert "Sem capa" in saida and "Sem ID Qobuz da faixa" in saida
    assert "Sem ISRC" in saida and "tags --dry-run" in saida
    assert "Nenhuma capa embutida" in saida


def test_mp3_e_lido_do_mesmo_jeito(biblioteca, capsys):
    codigo, saida = _rodar(biblioteca["mp3"], capsys)
    assert codigo == 0 and "Mp3" in saida and "com perdas" in saida


def test_arquivo_ilegivel_devolve_erro_e_nao_quebra(biblioteca, capsys):
    codigo, saida = _rodar(os.path.join(biblioteca["pasta"], "04 ruim.flac"), capsys)
    assert codigo == 1 and "Não foi possível ler esse arquivo" in saida


def test_completo_acrescenta_integridade_e_volume(biblioteca, capsys):
    _, saida = _rodar(biblioteca["completo"], capsys, completo=True)
    assert "🧪 INTEGRIDADE E VOLUME" in saida
    assert "Áudio íntegro" in saida and "LUFS" in saida and "dBFS" in saida


def test_sem_completo_nao_roda_ffmpeg(biblioteca, capsys):
    _, saida = _rodar(biblioteca["completo"], capsys)
    assert "INTEGRIDADE E VOLUME" not in saida


def test_espectro_e_executado_quando_pedido(biblioteca, capsys, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        inspector,
        "_checar_genuinidade",
        lambda *a: chamadas.append(a) or {"disponivel": False, "motivo": "teste"},
    )
    _, saida = _rodar(biblioteca["completo"], capsys, espectro=True)
    assert len(chamadas) == 1 and "teste" in saida


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------
def test_json_de_arquivo_e_so_json(biblioteca, capsys):
    codigo, saida = _rodar(biblioteca["completo"], capsys, json_saida=True)
    dados = json.loads(saida)  # nada além do JSON (sem "[*] Lendo tags...")
    assert codigo == 0
    assert dados["campos"]["titulo"] == "Completo"
    assert dados["campos"]["track_id"] == "555"
    assert dados["capas"][0]["largura"] == 600
    assert dados["problemas"] == [] and "integridade" not in dados
    assert not any(chave.startswith("_") for chave in dados)


def test_json_completo_inclui_integridade_e_volume(biblioteca, capsys):
    _, saida = _rodar(biblioteca["completo"], capsys, json_saida=True, completo=True)
    dados = json.loads(saida)
    assert dados["integridade"]["ok"] is True
    assert dados["volume"]["integrado_lufs"] < 0


def test_json_de_pasta_traz_totais_e_faixas(biblioteca, capsys):
    codigo, saida = _rodar(biblioteca["pasta"], capsys, json_saida=True)
    dados = json.loads(saida)
    assert codigo == 0
    assert dados["totais"]["arquivos"] == 4 and dados["totais"]["ilegiveis"] == 1
    assert dados["totais"]["problemas"]["no_cover"] == 2
    assert [f["titulo"] for f in dados["faixas"]] == ["Completo", "Pobre", "Mp3"]
    assert dados["ilegiveis"][0]["arquivo"].endswith("04 ruim.flac")


# ---------------------------------------------------------------------------
# --resumo
# ---------------------------------------------------------------------------
def test_resumo_da_pasta(biblioteca, capsys):
    codigo, saida = _rodar(biblioteca["pasta"], capsys, resumo=True)
    assert codigo == 0
    assert "📚 RESUMO DA PASTA" in saida and "Arquivos de áudio" in saida
    assert "Dua Lipa — Completo" in saida and "capa 600x600" in saida
    assert "sem capa" in saida
    assert "04 ruim.flac" in saida and "não foi possível ler" in saida
    assert "📊 TOTAIS" in saida and "Faixas lidas" in saida
    assert "O QUE FALTA CORRIGIR" in saida and "tags --report" in saida
    # o arquivo com problema é identificado pelo nome
    assert "arquivo: 02 pobre.flac" in saida


def test_resumo_de_pasta_vazia_avisa(tmp_path, capsys):
    codigo, saida = _rodar(str(tmp_path), capsys, resumo=True)
    assert codigo == 0 and "Nenhum arquivo de áudio" in saida


def test_resumo_completo_marca_o_que_nao_decodifica(biblioteca, capsys):
    _, saida = _rodar(biblioteca["pasta"], capsys, resumo=True, completo=True)
    assert "CORROMPIDO" not in saida.split("04 ruim.flac")[0]  # os bons estão íntegros
    assert "Áudio corrompido" in saida


def test_resumo_de_pasta_sem_problemas(biblioteca, tmp_path, capsys):
    shutil.copy(biblioteca["completo"], tmp_path / "01 completo.flac")
    _, saida = _rodar(str(tmp_path), capsys, resumo=True)
    assert "Nenhum problema encontrado" in saida
    assert "O QUE FALTA CORRIGIR" not in saida


# ---------------------------------------------------------------------------
# --comparar
# ---------------------------------------------------------------------------
def test_comparar_roda_o_tags_em_simulacao_numa_copia(biblioteca, monkeypatch, capsys):
    chamadas = {}

    async def falso(pasta, client, settings, **kwargs):
        chamadas["arquivos"] = os.listdir(pasta)
        chamadas["pasta"] = pasta
        chamadas["kwargs"] = kwargs
        chamadas["client"] = client

    monkeypatch.setattr("qobuz_dl.retro_tags.retag_directory", falso)
    original = os.stat(biblioteca["completo"]).st_mtime_ns
    codigo = asyncio.run(
        inspector.comparar_com_qobuz("CLIENTE", object(), biblioteca["completo"])
    )
    assert codigo == 0
    assert chamadas["arquivos"] == ["01 completo.flac"]  # só a cópia do arquivo
    assert chamadas["kwargs"] == {"dry_run": True}  # nunca grava
    assert chamadas["client"] == "CLIENTE"
    assert not os.path.exists(chamadas["pasta"])  # a pasta temporária foi apagada
    assert os.stat(biblioteca["completo"]).st_mtime_ns == original
    assert "simulação" in capsys.readouterr().out


def test_comparar_recusa_pasta_e_formato_sem_suporte(biblioteca, tmp_path, capsys):
    assert (
        asyncio.run(inspector.comparar_com_qobuz(None, None, biblioteca["pasta"])) == 1
    )
    outro = tmp_path / "faixa.ogg"
    outro.write_bytes(b"x")
    assert asyncio.run(inspector.comparar_com_qobuz(None, None, str(outro))) == 1
    saida = capsys.readouterr().out
    assert "ARQUIVO" in saida and "FLAC e MP3" in saida


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------
def test_parser_aceita_as_flags_novas():
    args = qobuz_dl_args().parse_args(
        [
            "inspect",
            "faixa.flac",
            "--json",
            "--resumo",
            "--completo",
            "--sem-espectro",
            "--comparar",
        ]  # fmt: skip
    )
    assert args.caminho == "faixa.flac"
    assert args.json and args.resumo and args.completo
    assert args.sem_espectro and args.comparar


def test_parser_inspect_sem_nada_continua_valido():
    args = qobuz_dl_args().parse_args(["inspect"])
    assert args.caminho is None
    assert not (args.json or args.resumo or args.completo or args.comparar)


CONFIG_TESTE = (
    "[DEFAULT]\nemail = a@b.c\npassword = x\ndefault_folder = M\n"
    "default_quality = 6\ndefault_limit = 20\napp_id = 1\nsecrets = s\n"
    "no_m3u = false\nalbums_only = false\nno_fallback = false\n"
    "og_cover = false\nembed_art = false\nno_cover = false\n"
    "no_database = false\nsmart_discography = false\n"
    "folder_format = f\ntrack_format = t\ndownload_booklets = false\n"
)


def _dispatch(monkeypatch, tmp_path, *argv):
    """Roda `async_main` até o `run_inspector` (trocado por um espião)."""
    monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
    monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
    (tmp_path / "config.ini").write_text("[DEFAULT]\n")
    monkeypatch.setattr(sys, "argv", ["qobuz-dl", *argv])
    monkeypatch.setattr(cli, "check_for_updates", lambda: None)
    visto = {}

    async def espiao(caminho=None, **kwargs):
        visto.update(caminho=caminho, **kwargs)
        return 0

    monkeypatch.setattr(inspector, "run_inspector", espiao)
    with pytest.raises(SystemExit) as saida:
        asyncio.run(cli.async_main())
    return saida.value.code, visto


def test_cli_repassa_as_flags_ao_inspetor(monkeypatch, tmp_path):
    codigo, visto = _dispatch(
        monkeypatch, tmp_path, "inspect", "/musicas", "--json", "--completo",
        "--sem-espectro", "--resumo",
    )  # fmt: skip
    assert codigo == 0
    assert visto == {
        "caminho": "/musicas",
        "json_saida": True,
        "resumo": True,
        "completo": True,
        "espectro": False,
    }


def test_cli_padrao_do_inspetor(monkeypatch, tmp_path):
    _, visto = _dispatch(monkeypatch, tmp_path, "inspect")
    assert visto == {
        "caminho": None,
        "json_saida": False,
        "resumo": False,
        "completo": False,
        "espectro": True,
    }


def test_cli_comparar_autentica_e_chama_a_comparacao(biblioteca, monkeypatch, tmp_path):
    """`inspect ARQUIVO --comparar` vai pelo caminho com login (não pelo offline)."""
    import qobuz_dl.qopy as qopy

    (tmp_path / "config.ini").write_text(CONFIG_TESTE)
    monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
    monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
    monkeypatch.setattr(cli, "check_for_updates", lambda: None)
    monkeypatch.setattr(
        sys, "argv", ["qobuz-dl", "inspect", biblioteca["completo"], "--comparar"]
    )
    eventos = {}

    class ClienteFalso:
        @classmethod
        async def create(cls, *args, **kwargs):
            eventos["login"] = True
            return cls()

        async def close(self):
            eventos["fechou"] = True

    async def comparacao(client, settings, caminho):
        eventos["caminho"] = caminho
        eventos["client"] = type(client).__name__
        return 0

    async def offline_nao_deveria_rodar(*a, **k):
        raise AssertionError("--comparar não pode usar o caminho offline")

    monkeypatch.setattr(qopy, "Client", ClienteFalso)
    monkeypatch.setattr(inspector, "comparar_com_qobuz", comparacao)
    monkeypatch.setattr(inspector, "run_inspector", offline_nao_deveria_rodar)
    with pytest.raises(SystemExit) as saida:
        asyncio.run(cli.async_main())
    assert saida.value.code == 0
    assert eventos == {
        "login": True,
        "fechou": True,
        "caminho": biblioteca["completo"],
        "client": "ClienteFalso",
    }


def test_cli_comparar_sem_arquivo_avisa_e_sai(monkeypatch, tmp_path, capsys):
    (tmp_path / "config.ini").write_text(CONFIG_TESTE)
    monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
    monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
    monkeypatch.setattr(cli, "check_for_updates", lambda: None)
    monkeypatch.setattr(
        sys, "argv", ["qobuz-dl", "inspect", str(tmp_path), "--comparar"]
    )
    with pytest.raises(SystemExit) as saida:
        asyncio.run(cli.async_main())
    assert saida.value.code == 1
    assert "precisa de um ARQUIVO" in capsys.readouterr().out


def test_sem_caminho_abre_o_navegador_e_mostra_o_relatorio_novo(
    biblioteca, monkeypatch, capsys
):
    escolhido = {}

    async def navegador(pasta):
        escolhido["pasta"] = pasta
        return biblioteca["completo"]

    monkeypatch.setattr(inspector, "_navegar_arquivos", navegador)
    codigo, saida = _rodar(biblioteca["pasta"], capsys, resumo=False)
    # (com um ARQUIVO nunca navega; com uma PASTA sem --resumo, navega)
    assert codigo == 0 and escolhido["pasta"] == biblioteca["pasta"]
    assert "🩺 DIAGNÓSTICO" in saida and "🎤 LETRAS" in saida


def test_navegador_cancelado_nao_gera_relatorio(biblioteca, monkeypatch, capsys):
    async def cancelado(pasta):
        return None

    monkeypatch.setattr(inspector, "_navegar_arquivos", cancelado)
    codigo, saida = _rodar(biblioteca["pasta"], capsys)
    assert codigo == 0 and "Nenhum arquivo escolhido" in saida
    assert "INSPETOR DE ÁUDIO" not in saida
