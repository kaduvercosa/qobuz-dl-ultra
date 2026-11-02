"""Comando `retry`: descobre as faixas pendentes pelo .report.json e as repete."""

import json
import os

import pytest

from qobuz_dl import postprocess, retry_missing

pytestmark = pytest.mark.unit


def _faixa(numero, track_id, titulo, situacao, motivo=""):
    return {
        "numero": numero,
        "id": track_id,
        "identificacao": {"titulo": titulo},
        "download": {"situacao": situacao, "motivo": motivo},
    }


def _escrever_report(pasta, faixas, album_id="A1", tipo="album"):
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, postprocess.REPORT_FILENAME)
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(
            {
                "tipo": tipo,
                "identificacao": {"id": album_id, "titulo": "Random Access Memories"},
                "faixas": faixas,
            },
            arquivo,
        )
    return caminho


def _situacoes(pasta):
    with open(os.path.join(pasta, postprocess.REPORT_FILENAME), encoding="utf-8") as f:
        return {x["id"]: x["download"]["situacao"] for x in json.load(f)["faixas"]}


class TestReadPending:
    def test_lista_so_o_que_nao_foi_concluido(self, tmp_path):
        pasta = str(tmp_path / "Album")
        _escrever_report(
            pasta,
            [
                _faixa("01", "1", "Give Life Back", "concluido"),
                _faixa("03", "3", "Giorgio by Moroder", "pulada", "Erro de API"),
                _faixa("07", "7", "Fragments of Time", "falha"),
            ],
        )
        pendente = retry_missing.read_pending(pasta)
        assert pendente is not None
        assert pendente.album_id == "A1"
        assert [t.id for t in pendente.tracks] == ["3", "7"]
        assert pendente.tracks[0].situacao == "pulada"
        assert pendente.tracks[0].motivo == "Erro de API"

    def test_status_legado_ok_conta_como_concluido(self, tmp_path):
        pasta = str(tmp_path / "Album")
        _escrever_report(pasta, [_faixa("01", "1", "A", "ok")])
        assert retry_missing.read_pending(pasta) is None

    def test_sem_pendencias_ou_sem_relatorio_devolve_none(self, tmp_path):
        assert retry_missing.read_pending(str(tmp_path)) is None
        pasta = str(tmp_path / "Album")
        _escrever_report(pasta, [_faixa("01", "1", "A", "concluido")])
        assert retry_missing.read_pending(pasta) is None

    def test_ignora_relatorio_que_nao_e_de_album(self, tmp_path):
        pasta = str(tmp_path / "Playlist")
        _escrever_report(pasta, [_faixa("01", "1", "A", "falha")], tipo="playlist")
        assert retry_missing.read_pending(pasta) is None

    def test_relatorio_sem_id_de_album_e_ignorado(self, tmp_path):
        pasta = str(tmp_path / "Album")
        _escrever_report(pasta, [_faixa("01", "1", "A", "falha")], album_id="")
        assert retry_missing.read_pending(pasta) is None


class TestFindPending:
    def test_procura_nas_subpastas_e_pula_in_progress(self, tmp_path):
        _escrever_report(
            str(tmp_path / "Album" / "A [FLAC 24]"),
            [_faixa("01", "1", "A", "pulada")],
            album_id="A1",
        )
        _escrever_report(
            str(tmp_path / "Album" / "B [FLAC 16]"),
            [_faixa("01", "9", "B", "concluido")],
            album_id="B1",
        )
        _escrever_report(
            str(tmp_path / "[IN PROGRESS] C"),
            [_faixa("01", "5", "C", "falha")],
            album_id="C1",
        )
        achados = retry_missing.find_pending(str(tmp_path))
        assert [a.album_id for a in achados] == ["A1"]

    def test_album_folder_for_retry_tira_o_prefixo_incomplete(self):
        assert retry_missing.album_folder_for_retry(
            os.path.join("musica", "[INCOMPLETE] Album X [FLAC 24]")
        ) == os.path.join("musica", "Album X [FLAC 24]")
        assert retry_missing.album_folder_for_retry(
            os.path.join("musica", "Album X")
        ) == os.path.join("musica", "Album X")


class FakeApp:
    """Imita QobuzDL.retry_album: marca como concluídas as faixas em `resolver`."""

    def __init__(self, resolver=()):
        self.resolver = set(resolver)
        self.chamadas = []

    async def retry_album(self, album_id, album_dir, track_ids):
        self.chamadas.append((album_id, album_dir, list(track_ids)))
        caminho = os.path.join(album_dir, postprocess.REPORT_FILENAME)
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        for faixa in dados["faixas"]:
            if faixa["id"] in self.resolver:
                faixa["download"]["situacao"] = "concluido"
        with open(caminho, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo)
        return True


class TestRunRetry:
    async def test_baixa_o_que_deu_e_apaga_o_placeholder(self, tmp_path):
        pasta = str(tmp_path / "Album")
        _escrever_report(
            pasta,
            [
                _faixa("03", "3", "Giorgio by Moroder", "pulada"),
                _faixa("07", "7", "Fragments of Time", "pulada"),
            ],
        )
        for nome in (
            "03. Giorgio by Moroder [INDISPONÍVEL].missing.txt",
            "07. Fragments of Time [INDISPONÍVEL].missing.txt",
        ):
            open(os.path.join(pasta, nome), "w", encoding="utf-8").close()

        app = FakeApp(resolver={"3"})
        codigo = await retry_missing.run_retry(app, str(tmp_path))

        assert codigo == 0  # a que segue indisponível não é falha
        assert app.chamadas == [("A1", pasta, ["3", "7"])]
        assert _situacoes(pasta) == {"3": "concluido", "7": "pulada"}
        restantes = sorted(os.listdir(pasta))
        assert "03. Giorgio by Moroder [INDISPONÍVEL].missing.txt" not in restantes
        assert "07. Fragments of Time [INDISPONÍVEL].missing.txt" in restantes

    async def test_falha_que_persiste_devolve_codigo_1(self, tmp_path):
        pasta = str(tmp_path / "Album")
        _escrever_report(pasta, [_faixa("01", "1", "A", "falha")])
        assert await retry_missing.run_retry(FakeApp(), str(tmp_path)) == 1

    async def test_sem_pendencias_nao_chama_o_app(self, tmp_path):
        app = FakeApp()
        assert await retry_missing.run_retry(app, str(tmp_path)) == 0
        assert app.chamadas == []

    async def test_pasta_inexistente_devolve_2(self, tmp_path):
        codigo = await retry_missing.run_retry(FakeApp(), str(tmp_path / "nao_existe"))
        assert codigo == 2
