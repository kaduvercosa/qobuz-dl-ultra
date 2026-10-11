"""Testes de qobuz_dl/track_identity.py e do casamento por ID no .m3u.

Cenário que motivou o módulo: na pasta única de playlist o nome do arquivo é
"{artista} - {título}". Duas faixas com mesmo artista e título, mas de álbuns
diferentes (IDs Qobuz diferentes), colidiam no mesmo caminho -- a segunda era
"pulada (já existe)" e contada como sucesso, ou sobrescrevia a primeira no
modo paralelo. Aqui garantimos que a identidade é o ID, nunca o nome.

Sem rede, sem áudio real e sem depender do mutagen para a lógica de reserva.
"""

import random
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from qobuz_dl import track_identity as ti

pytestmark = pytest.mark.unit


def _sanitize(texto):
    """Sanitização mínima usada só nos testes (a real vem do downloader)."""
    return "".join("_" if c in '<>:"/\\|?*' else c for c in texto).strip()


@pytest.fixture(autouse=True)
def _reservas_limpas():
    ti._claims.clear()
    yield
    ti._claims.clear()


@pytest.fixture
def ids_em_disco(monkeypatch):
    """Substitui a leitura de tags: o 'ID gravado' é o conteúdo 'id:<n>'."""

    def _ler(path):
        try:
            with open(path, encoding="utf-8") as f:
                texto = f.read()
        except OSError:
            return None
        return texto[3:] if texto.startswith("id:") else None

    monkeypatch.setattr(ti, "read_track_id", _ler)


def _grava(path, track_id):
    path.write_text(f"id:{track_id}", encoding="utf-8")


# --------------------------------------------------------------------
# build_name_stems
# --------------------------------------------------------------------
class TestBuildNameStems:
    def test_tres_candidatos_do_simples_ao_especifico(self):
        stems = ti.build_name_stems(
            "Artista - Musica", "Album B", "42", sanitize=_sanitize
        )
        assert stems == [
            "Artista - Musica",
            "Artista - Musica (Album B)",
            "Artista - Musica (Album B) [42]",
        ]

    def test_primeiro_candidato_e_o_nome_antigo(self):
        """Compatibilidade: bibliotecas existentes continuam reconhecidas."""
        stems = ti.build_name_stems("A - B", "Alb", "1", sanitize=_sanitize)
        assert stems[0] == _sanitize("A - B")

    def test_sem_album_usa_id_direto(self):
        stems = ti.build_name_stems("A - B", None, "7", sanitize=_sanitize)
        assert stems == ["A - B", "A - B [7]"]
        stems = ti.build_name_stems("A - B", "   ", "7", sanitize=_sanitize)
        assert stems == ["A - B", "A - B [7]"]

    def test_album_com_caracteres_invalidos_e_sanitizado(self):
        stems = ti.build_name_stems("A - B", "AC/DC: Live", "7", sanitize=_sanitize)
        assert "/" not in stems[1] and ":" not in stems[1]

    def test_nome_longo_preserva_album_e_id_no_final(self):
        base = "Artista " + "x" * 300 + " - Titulo"
        stems = ti.build_name_stems(
            base, "Album Longo", "123456789", sanitize=_sanitize
        )
        assert all(len(s) <= 180 for s in stems)
        assert stems[-1].endswith("[123456789]")
        assert len(set(stems)) == len(stems)


# --------------------------------------------------------------------
# resolve_output_path
# --------------------------------------------------------------------
class TestResolveOutputPath:
    def _cands(self, tmp_path):
        return [
            str(tmp_path / "A - M.flac"),
            str(tmp_path / "A - M (Alb2).flac"),
            str(tmp_path / "A - M (Alb2) [2].flac"),
        ]

    def test_livre_usa_o_primeiro_e_reserva(self, tmp_path, ids_em_disco):
        c = self._cands(tmp_path)
        claimed = []
        path, estado = ti.resolve_output_path(c, "1", claimed)
        assert (path, estado) == (c[0], ti.STATE_NEW)
        assert claimed == [c[0]]

    def test_arquivo_de_outro_id_vai_para_o_proximo_candidato(
        self, tmp_path, ids_em_disco
    ):
        c = self._cands(tmp_path)
        _grava(tmp_path / "A - M.flac", "1")
        path, estado = ti.resolve_output_path(c, "2")
        assert (path, estado) == (c[1], ti.STATE_NEW)

    def test_arquivo_com_o_mesmo_id_e_reconhecido(self, tmp_path, ids_em_disco):
        c = self._cands(tmp_path)
        _grava(tmp_path / "A - M.flac", "1")
        path, estado = ti.resolve_output_path(c, "1")
        assert (path, estado) == (c[0], ti.STATE_EXISTING)

    def test_reconhece_o_proprio_arquivo_mesmo_em_candidato_posterior(
        self, tmp_path, ids_em_disco
    ):
        """A faixa 2 gravou '(Alb2)' numa execução; na seguinte o primeiro
        candidato pertence à faixa 1 -- a 2 deve achar o seu, sem duplicar."""
        c = self._cands(tmp_path)
        _grava(tmp_path / "A - M.flac", "1")
        _grava(tmp_path / "A - M (Alb2).flac", "2")
        assert ti.resolve_output_path(c, "2") == (c[1], ti.STATE_EXISTING)
        assert ti.resolve_output_path(c, "1") == (c[0], ti.STATE_EXISTING)

    def test_arquivo_sem_id_legivel_nao_e_tratado_como_da_faixa(
        self, tmp_path, ids_em_disco
    ):
        """Nunca descarta uma faixa em silêncio: arquivo ilegível/sem tag não
        é 'a mesma faixa' -- a nova vai para o próximo nome livre."""
        c = self._cands(tmp_path)
        (tmp_path / "A - M.flac").write_text("lixo", encoding="utf-8")
        path, estado = ti.resolve_output_path(c, "9")
        assert (path, estado) == (c[1], ti.STATE_NEW)

    def test_reserva_em_andamento_de_outra_faixa_e_respeitada(
        self, tmp_path, ids_em_disco
    ):
        c = self._cands(tmp_path)
        assert ti.resolve_output_path(c, "1")[0] == c[0]
        assert ti.resolve_output_path(c, "2")[0] == c[1]
        assert ti.resolve_output_path(c, "3")[0] == c[2]

    def test_mesma_faixa_em_andamento_nao_baixa_duas_vezes(
        self, tmp_path, ids_em_disco
    ):
        c = self._cands(tmp_path)
        assert ti.resolve_output_path(c, "1") == (c[0], ti.STATE_NEW)
        assert ti.resolve_output_path(c, "1") == (c[0], ti.STATE_IN_PROGRESS)

    def test_release_libera_o_nome(self, tmp_path, ids_em_disco):
        c = self._cands(tmp_path)
        claimed = []
        ti.resolve_output_path(c, "1", claimed)
        ti.release_claims(claimed)
        assert ti.resolve_output_path(c, "2")[0] == c[0]

    def test_reserva_ignora_maiusculas_e_minusculas(self, tmp_path, ids_em_disco):
        ti.resolve_output_path([str(tmp_path / "Song.flac")], "1")
        estado = ti.resolve_output_path(
            [str(tmp_path / "SONG.flac"), str(tmp_path / "SONG [2].flac")], "2"
        )
        assert estado == (str(tmp_path / "SONG [2].flac"), ti.STATE_NEW)

    def test_sem_candidatos_levanta(self):
        with pytest.raises(ValueError):
            ti.resolve_output_path([], "1")


class TestConcorrencia:
    def test_threads_simultaneas_nunca_recebem_o_mesmo_caminho(
        self, tmp_path, ids_em_disco
    ):
        n = 40
        resultados = {}
        barreira = threading.Barrier(n)

        def _tarefa(i):
            tid = str(1000 + i)
            stems = ti.build_name_stems(
                "Artista - Musica", "Album", tid, sanitize=_sanitize
            )
            cands = [str(tmp_path / f"{s}.flac") for s in stems]
            barreira.wait()
            resultados[tid] = ti.resolve_output_path(cands, tid)

        # Só o 3º candidato carrega o ID; com N faixas iguais, as 3 primeiras
        # escolhas ficam com os nomes compartilhados e as demais, com o ID.
        threads = [threading.Thread(target=_tarefa, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        caminhos = [p for p, _ in resultados.values()]
        assert len(caminhos) == n
        assert len(set(caminhos)) == n, "duas faixas receberam o mesmo caminho"

    @pytest.mark.parametrize("semente", range(5))
    def test_ordem_qualquer_e_reexecucao_sao_estaveis(
        self, tmp_path, ids_em_disco, semente
    ):
        """Simula 'baixar' em ordem embaralhada e depois repetir: cada ID tem
        que reencontrar o MESMO arquivo, sem criar duplicatas."""
        ids = [str(i) for i in range(1, 7)]

        def _rodada(ordem):
            achados = {}
            for tid in ordem:
                stems = ti.build_name_stems(
                    "A - M", "Mesmo Album", tid, sanitize=_sanitize
                )
                cands = [str(tmp_path / f"{s}.flac") for s in stems]
                claimed = []
                path, estado = ti.resolve_output_path(cands, tid, claimed)
                if estado == ti.STATE_NEW:
                    _grava(Path(path), tid)
                ti.release_claims(claimed)
                achados[tid] = (path, estado)
            return achados

        rng = random.Random(semente)
        ordem1 = ids[:]
        rng.shuffle(ordem1)
        primeira = _rodada(ordem1)
        assert all(e == ti.STATE_NEW for _, e in primeira.values())
        assert len({p for p, _ in primeira.values()}) == len(ids)

        ordem2 = ids[:]
        rng.shuffle(ordem2)
        segunda = _rodada(ordem2)
        assert all(e == ti.STATE_EXISTING for _, e in segunda.values())
        assert {k: v[0] for k, v in segunda.items()} == {
            k: v[0] for k, v in primeira.items()
        }
        assert len(list(tmp_path.glob("*.flac"))) == len(ids)


# --------------------------------------------------------------------
# read_track_id (com fakes de mutagen, no estilo dos outros testes)
# --------------------------------------------------------------------
class TestReadTrackId:
    def test_arquivo_inexistente_devolve_none(self, tmp_path):
        assert ti.read_track_id(str(tmp_path / "nao.flac")) is None

    def test_extensao_desconhecida_devolve_none(self, tmp_path):
        p = tmp_path / "a.ogg"
        p.write_bytes(b"x")
        assert ti.read_track_id(str(p)) is None

    def test_flac_le_qobuztrackid(self, tmp_path, monkeypatch):
        mutagen_flac = pytest.importorskip("mutagen.flac")

        class FakeFLAC(dict):
            def __init__(self, _p):
                super().__init__(QOBUZTRACKID=[" 321 "])

        monkeypatch.setattr(mutagen_flac, "FLAC", FakeFLAC)
        assert ti.read_track_id(str(tmp_path / "a.flac")) == "321"

    def test_flac_sem_tag_usa_comentario_trk_id(self, tmp_path, monkeypatch):
        mutagen_flac = pytest.importorskip("mutagen.flac")

        class FakeFLAC(dict):
            def __init__(self, _p):
                super().__init__(COMMENT=["Qobuz | 16b | Trk ID: 555"])

        monkeypatch.setattr(mutagen_flac, "FLAC", FakeFLAC)
        assert ti.read_track_id(str(tmp_path / "a.flac")) == "555"

    def test_mp3_le_txxx_qobuztrackid(self, tmp_path, monkeypatch):
        mutagen_id3 = pytest.importorskip("mutagen.id3")
        frame = SimpleNamespace(desc="QOBUZTRACKID", text=["777"])

        class FakeID3:
            def __init__(self, _p):
                pass

            def getall(self, chave):
                return [frame] if chave == "TXXX" else []

        monkeypatch.setattr(mutagen_id3, "ID3", FakeID3)
        assert ti.read_track_id(str(tmp_path / "a.mp3")) == "777"

    def test_mp3_antigo_sem_txxx_usa_comentario(self, tmp_path, monkeypatch):
        mutagen_id3 = pytest.importorskip("mutagen.id3")
        comm = SimpleNamespace(text=["Qobuz | Rel: 2024 | Trk ID: 888"])

        class FakeID3:
            def __init__(self, _p):
                pass

            def getall(self, chave):
                return [comm] if chave == "COMM" else []

        monkeypatch.setattr(mutagen_id3, "ID3", FakeID3)
        assert ti.read_track_id(str(tmp_path / "a.mp3")) == "888"

    def test_erro_de_leitura_devolve_none(self, tmp_path, monkeypatch):
        mutagen_flac = pytest.importorskip("mutagen.flac")

        def _boom(_p):
            raise RuntimeError("corrompido")

        monkeypatch.setattr(mutagen_flac, "FLAC", _boom)
        assert ti.read_track_id(str(tmp_path / "a.flac")) is None


# --------------------------------------------------------------------
# make_m3u: nunca ligar um item ao arquivo de OUTRA faixa por título/ISRC
# --------------------------------------------------------------------
class TestM3uComFaixasHomonimas:
    def _fakes(self, monkeypatch, tags_por_arquivo):
        import mutagen
        import mutagen.flac

        class FakeFLAC(dict):
            def __init__(self, path):
                nome = path.replace("\\", "/").rsplit("/", 1)[-1]
                super().__init__(tags_por_arquivo[nome])

        class FakeFile:
            info = SimpleNamespace(length=100)

        monkeypatch.setattr(mutagen, "File", lambda _p: FakeFile())
        monkeypatch.setattr(mutagen.flac, "FLAC", FakeFLAC)

    def _linhas(self, tmp_path):
        texto = (tmp_path / f"{tmp_path.name}.m3u8").read_text("utf-8")
        return [x for x in texto.splitlines() if x and not x.startswith("#")]

    def test_homonimas_mantem_a_ordem_pelo_id(self, tmp_path, monkeypatch):
        pytest.importorskip("mutagen")
        from qobuz_dl import utils

        for nome in ("A - M.flac", "A - M (Alb2).flac"):
            (tmp_path / nome).write_bytes(b"x")
        self._fakes(
            monkeypatch,
            {
                "A - M.flac": {"QOBUZTRACKID": ["1"], "TITLE": ["M"], "ARTIST": ["A"]},
                "A - M (Alb2).flac": {
                    "QOBUZTRACKID": ["2"],
                    "TITLE": ["M"],
                    "ARTIST": ["A"],
                },
            },
        )
        remoto = [{"id": 2, "title": "M"}, {"id": 1, "title": "M"}]
        utils.make_m3u(str(tmp_path), remoto)
        assert self._linhas(tmp_path) == ["A - M (Alb2).flac", "A - M.flac"]

    def test_faixa_ausente_nao_herda_o_arquivo_da_homonima(self, tmp_path, monkeypatch):
        pytest.importorskip("mutagen")
        from qobuz_dl import utils

        (tmp_path / "A - M.flac").write_bytes(b"x")
        self._fakes(
            monkeypatch,
            {
                "A - M.flac": {
                    "QOBUZTRACKID": ["1"],
                    "ISRC": ["ISRC1"],
                    "TITLE": ["M"],
                    "ARTIST": ["A"],
                }
            },
        )
        # A faixa 2 (mesmo título e mesmo ISRC) NÃO foi baixada: não pode
        # aparecer na m3u apontando para o arquivo da faixa 1.
        remoto = [
            {"id": 1, "title": "M", "isrc": "ISRC1"},
            {"id": 2, "title": "M", "isrc": "ISRC1"},
        ]
        utils.make_m3u(str(tmp_path), remoto)
        assert self._linhas(tmp_path) == ["A - M.flac"]
