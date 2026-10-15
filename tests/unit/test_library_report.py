"""Relatório da biblioteca (`tags --report`): agregação, CSV, HTML e leitura real."""

import csv
import io
import os
import shutil
import subprocess

import pytest

from qobuz_dl import library_report as lr
from qobuz_dl import metadata
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


def _rec(folder="/m/Album", file="01.flac", issues=(), **kw):
    base = {
        "path": os.path.join(folder, file),
        "folder": folder,
        "file": file,
        "artist": "Artista",
        "album": "Album",
        "title": "Faixa",
        "cover": (1000, 1000),
        "track_id": "123",
        "album_id": "abc",
        "issues": list(issues),
    }
    base.update(kw)
    return base


class TestAgregacao:
    def test_totais_e_ordem_do_pior_para_o_melhor(self):
        records = [
            _rec("/m/Bom", "1.flac"),
            _rec("/m/Ruim", "1.flac", ["no_cover", "no_isrc"]),
            _rec("/m/Ruim", "2.flac", ["no_cover"]),
            _rec("/m/Meio", "1.flac", ["no_isrc"]),
        ]
        resumo = lr.summarize(records)
        assert resumo["total"] == 4 and resumo["with_issues"] == 3
        assert resumo["per_issue"]["no_cover"] == 2
        assert [a["folder"] for a in resumo["albums"]] == [
            "/m/Ruim",
            "/m/Meio",
            "/m/Bom",
        ]
        assert resumo["albums"][0]["with_issues"] == 2

    def test_lista_vazia(self):
        resumo = lr.summarize([])
        assert resumo["total"] == 0 and resumo["albums"] == []


class TestCsv:
    def test_so_arquivos_com_problema_e_cabecalho(self):
        records = [
            _rec(file="ok.flac"),
            _rec(file="x.flac", issues=["no_cover", "no_isrc"], cover=None),
        ]
        linhas = list(csv.reader(io.StringIO(lr.to_csv(records, "/m"))))
        assert linhas[0] == lr.CSV_COLUMNS
        assert len(linhas) == 2
        assert linhas[1][1] == "x.flac"
        assert linhas[1][0] == "Album"  # pasta relativa à raiz
        assert linhas[1][-1] == "Sem capa; Sem ISRC"

    def test_virgula_e_aspas_nos_campos_sao_escapadas(self):
        record = _rec(issues=["no_isrc"], artist='Tyler, "The" Creator')
        linhas = list(csv.reader(io.StringIO(lr.to_csv([record]))))
        assert linhas[1][2] == 'Tyler, "The" Creator'


class TestHtml:
    def test_estrutura_e_contagens(self):
        records = [_rec(issues=["no_cover"]), _rec(file="02.flac")]
        texto = lr.to_html(records, "/m")
        assert texto.startswith("<!doctype html>")
        assert "<b>2</b>faixas analisadas" in texto
        assert "Sem capa" in texto and "01.flac" in texto
        assert "http://" not in texto and "https://" not in texto  # sem rede

    def test_escapa_html_dos_nomes(self):
        record = _rec(issues=["no_isrc"], artist="<script>alert(1)</script>")
        texto = lr.to_html([record], "/m")
        assert "<script>alert(1)</script>" not in texto
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in texto

    def test_biblioteca_sem_problemas(self):
        assert "Nenhum problema encontrado" in lr.to_html([_rec()], "/m")


class TestGravacao:
    def test_html_por_padrao_e_csv_pela_extensao(self, tmp_path):
        records = [_rec(issues=["no_cover"])]
        html_path = lr.write_report(records, str(tmp_path / "sub" / "r.html"))
        csv_path = lr.write_report(records, str(tmp_path / "r.csv"))
        assert open(html_path, encoding="utf-8").read().startswith("<!doctype")
        bruto = open(csv_path, "rb").read()
        assert bruto.startswith(b"\xef\xbb\xbf")  # BOM para o Excel

    def test_erro_de_gravacao_propaga_oserror(self, tmp_path):
        arquivo = tmp_path / "arquivo"
        arquivo.write_text("x")
        with pytest.raises(OSError):
            lr.write_report([_rec()], str(arquivo / "r.html"))


# --------------------------------------------------------------------------
# Leitura de arquivos de verdade
# --------------------------------------------------------------------------
FFMPEG = shutil.which("ffmpeg")
try:
    from mutagen.flac import FLAC

    _MUTAGEN_REAL = isinstance(FLAC, type)
except Exception:  # noqa: BLE001
    _MUTAGEN_REAL = False

real = pytest.mark.skipif(
    not (FFMPEG and _MUTAGEN_REAL), reason="ffmpeg/mutagen reais indisponíveis"
)


def _ff(*args):
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", *args], check=True, capture_output=True
    )


def _audio(path, fmt):
    codec = ["-c:a", "flac"] if fmt == "flac" else ["-c:a", "libmp3lame"]
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", *codec, path)


def _capa(path, tamanho):
    _ff("-f", "lavfi", "-i", f"color=c=red:s={tamanho}:d=1", "-frames:v", "1", path)


def _item():
    return {
        "title": "Faixa",
        "version": "",
        "performer": {"name": "Artista"},
        "performers": "Artista, MainArtist",
        "composer": {"name": "Compositor"},
        "isrc": "US1234567890",
        "parental_warning": False,
        "id": 555,
        "audio_info": {},
        "track_number": 3,
        "media_number": 1,
        "maximum_bit_depth": 24,
        "maximum_sampling_rate": 96,
        "maximum_channel_count": 2,
        "duration": 245,
    }


def _album():
    return {
        "title": "Album",
        "version": "",
        "artist": {"name": "Artista"},
        "genre": {"name": "Rap"},
        "genres_list": [],
        "release_date_original": "2024-05-01",
        "copyright": "(C) 2024",
        "label": {"name": "Gravadora"},
        "upc": "1234567890123",
        "product_type": "album",
        "release_type": "album",
        "tracks_count": 10,
        "media_count": 1,
        "duration": 3000,
        "id": "alb123",
    }


def _gravar(fmt, arquivo, capa=None, item=None):
    funcao = metadata.tag_flac if fmt == "flac" else metadata.tag_mp3
    funcao(
        arquivo,
        os.path.dirname(arquivo),
        arquivo,
        item or _item(),
        _album(),
        False,
        bool(capa),
        settings=QobuzDLSettings(),
        embed_cover_path=capa,
    )


@real
@pytest.mark.parametrize("fmt", ["flac", "mp3"])
class TestInspecaoReal:
    def test_arquivo_completo_nao_tem_problemas(self, tmp_path, fmt):
        arq, capa = str(tmp_path / f"a.{fmt}"), str(tmp_path / "c.jpg")
        _audio(arq, fmt)
        _capa(capa, "600x600")
        _gravar(fmt, arq, capa)
        r = lr.inspect_file(arq)
        assert r["issues"] == [], r
        assert r["track_id"] == "555" and r["album_id"] == "alb123"
        assert r["cover"] == (600, 600)

    def test_sem_nada_acusa_tudo(self, tmp_path, fmt):
        arq = str(tmp_path / f"a.{fmt}")
        _audio(arq, fmt)
        issues = set(lr.inspect_file(arq)["issues"])
        assert {
            "no_cover",
            "no_track_id",
            "no_album_id",
            "no_isrc",
            "no_date",
            "no_title",
            "no_artist",
        } <= issues

    def test_capa_pequena_e_retangular(self, tmp_path, fmt):
        arq, capa = str(tmp_path / f"a.{fmt}"), str(tmp_path / "c.jpg")
        _audio(arq, fmt)
        _capa(capa, "300x200")
        _gravar(fmt, arq, capa)
        issues = lr.inspect_file(arq)["issues"]
        assert "small_cover" in issues and "bad_shape_cover" in issues
        assert "no_cover" not in issues

    def test_sem_isrc(self, tmp_path, fmt):
        arq, capa = str(tmp_path / f"a.{fmt}"), str(tmp_path / "c.jpg")
        _audio(arq, fmt)
        _capa(capa, "600x600")
        item = _item()
        item["isrc"] = ""
        _gravar(fmt, arq, capa, item)
        assert lr.inspect_file(arq)["issues"] == ["no_isrc"]


@real
def test_arquivo_corrompido_vira_ilegivel_sem_derrubar_a_varredura(tmp_path):
    ruim = tmp_path / "ruim.flac"
    ruim.write_bytes(b"isso nao e flac")
    bom = tmp_path / "bom.mp3"
    _audio(str(bom), "mp3")
    records = lr.scan_directory(str(tmp_path))
    por_nome = {r["file"]: r for r in records}
    assert por_nome["ruim.flac"]["issues"] == ["unreadable"]
    assert "unreadable" not in por_nome["bom.mp3"]["issues"]


def test_scan_ignora_outros_arquivos_e_reporta_progresso(tmp_path):
    (tmp_path / "nota.txt").write_text("x")
    visto = []
    assert lr.scan_directory(str(tmp_path), lambda a, b: visto.append((a, b))) == []
    assert visto == []


# --------------------------------------------------------------------------
# Comando: `tags --report` e flags de progresso
# --------------------------------------------------------------------------
class TestComando:
    @pytest.fixture
    def saida(self, monkeypatch):
        from qobuz_dl import ui

        linhas = []
        for nome in ("emit", "ok", "warn", "error", "step"):
            monkeypatch.setattr(
                ui, nome, lambda m="", *a, _n=nome, **k: linhas.append((_n, str(m)))
            )
        return linhas

    def test_pasta_inexistente(self, tmp_path, saida):
        from qobuz_dl.cli import _handle_tags_report

        assert _handle_tags_report(str(tmp_path / "nao"), str(tmp_path / "r.html")) == 1
        assert saida[-1][0] == "error"

    def test_pasta_sem_musicas(self, tmp_path, saida):
        from qobuz_dl.cli import _handle_tags_report

        assert _handle_tags_report(str(tmp_path), str(tmp_path / "r.html")) == 1
        assert not (tmp_path / "r.html").exists()

    def test_gera_o_arquivo_e_resume(self, tmp_path, saida, monkeypatch):
        from qobuz_dl import cli

        monkeypatch.setattr(
            lr,
            "scan_directory",
            lambda d, progress=None: [_rec(issues=["no_cover"]), _rec(file="2.flac")],
        )
        destino = str(tmp_path / "r.html")
        assert cli._handle_tags_report(str(tmp_path), destino) == 0
        assert os.path.isfile(destino)
        texto = "\n".join(m for _, m in saida)
        assert "2 faixas analisadas, 1 com problemas" in texto
        assert "Sem capa: 1" in texto and destino in texto

    def test_destino_invalido_da_erro_com_proxima_acao(
        self, tmp_path, saida, monkeypatch
    ):
        from qobuz_dl import cli

        monkeypatch.setattr(lr, "scan_directory", lambda d, progress=None: [_rec()])
        arquivo = tmp_path / "arq"
        arquivo.write_text("x")
        assert cli._handle_tags_report(str(tmp_path), str(arquivo / "r.html")) == 1
        assert "--report" in saida[-1][1]

    def test_flags_do_parser(self):
        from qobuz_dl.commands import qobuz_dl_args

        parser = qobuz_dl_args(
            default_quality=6, default_limit=20, default_folder="MinhaMusica"
        )
        a = parser.parse_args(["tags", "--report"])
        assert a.report == "relatorio_biblioteca.html"
        assert parser.parse_args(["tags", "--report", "x.csv"]).report == "x.csv"
        assert parser.parse_args(["tags"]).report is None
        assert parser.parse_args(["tags", "--resume"]).resume is True
        assert parser.parse_args(["tags", "--incremental"]).incremental is True
        with pytest.raises(SystemExit):
            parser.parse_args(["tags", "--resume", "--incremental"])
