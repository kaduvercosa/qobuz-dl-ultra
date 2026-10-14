"""`tags --suggest-names`: descobre artistas com vírgula no nome pelas tags."""

import shutil
import subprocess

import pytest

from qobuz_dl import name_suggest
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


def _arquivos(*listas, vezes=1):
    """Cada argumento é a lista de entradas de UM arquivo (um campo só)."""
    return [[list(entradas)] for entradas in listas for _ in range(vezes)]


class TestSuggestFromLists:
    def test_acha_tyler_the_creator(self):
        dados = _arquivos(
            ["Tyler", "The Creator"],
            ["Tyler", "The Creator", "Kali Uchis"],
            ["Frank Ocean", "Tyler", "The Creator"],
            ["Tyler", "The Creator", "Lil Wayne"],
        )
        assert name_suggest.suggest_from_lists(dados) == [
            {"name": "Tyler, The Creator", "files": 4}
        ]

    def test_dupla_que_tambem_aparece_separada_nao_e_sugerida(self):
        dados = _arquivos(
            ["Beyoncé", "Jay-Z"], ["Beyoncé", "Jay-Z"], ["Beyoncé", "Jay-Z"]
        ) + _arquivos(["Beyoncé"], ["Beyoncé"], ["Jay-Z"], ["Jay-Z"])
        assert name_suggest.suggest_from_lists(dados) == []

    def test_corrente_de_tres_pedacos_vira_um_nome_so(self):
        dados = _arquivos(["Crosby", "Stills", "Nash & Young"], vezes=4)
        assert name_suggest.suggest_from_lists(dados) == [
            {"name": "Crosby, Stills, Nash & Young", "files": 4}
        ]

    def test_poucas_faixas_nao_bastam(self):
        dados = _arquivos(["Tyler", "The Creator"], vezes=2)
        assert name_suggest.suggest_from_lists(dados) == []
        assert name_suggest.suggest_from_lists(dados, min_files=2)

    def test_nome_ja_protegido_nao_e_sugerido_de_novo(self):
        dados = _arquivos(["Tyler", "The Creator"], vezes=5)
        assert (
            name_suggest.suggest_from_lists(dados, protected=["tyler, the creator"])
            == []
        )

    def test_um_arquivo_conta_uma_vez_mesmo_com_varios_campos(self):
        # ARTIST e ALBUMARTIST do mesmo arquivo: não pode contar em dobro.
        um_arquivo = [["Tyler", "The Creator"], ["Tyler", "The Creator"]]
        dados = [um_arquivo, um_arquivo]  # 2 arquivos só
        assert name_suggest.suggest_from_lists(dados) == []

    def test_lista_vazia(self):
        assert name_suggest.suggest_from_lists([]) == []
        assert name_suggest.suggest_from_lists([[], [[]]]) == []

    def test_ordena_do_mais_frequente_para_o_menos(self):
        dados = _arquivos(["Aa", "Bb"], vezes=3) + _arquivos(["Cc", "Dd"], vezes=6)
        nomes = [s["name"] for s in name_suggest.suggest_from_lists(dados)]
        assert nomes == ["Cc, Dd", "Aa, Bb"]


class TestEntries:
    def test_texto_unico_e_repartido_por_virgula(self):
        assert name_suggest._entries(["Tyler, The Creator, Kali Uchis"]) == [
            "Tyler",
            "The Creator",
            "Kali Uchis",
        ]

    def test_varias_entradas_sao_mantidas(self):
        assert name_suggest._entries(["A", " B ", ""]) == ["A", "B"]


class TestConfigLine:
    def test_junta_o_existente_com_as_sugestoes_sem_repetir(self):
        linha = name_suggest.config_line(
            ["Banda, Do Fulano"],
            [{"name": "Tyler, The Creator", "files": 3}, {"name": "banda, do fulano"}],
        )
        assert linha == "artists_with_comma = Banda, Do Fulano; Tyler, The Creator"


# --------------------------------------------------------------------------
# Com arquivos de verdade (ffmpeg + mutagen reais)
# --------------------------------------------------------------------------
FFMPEG = shutil.which("ffmpeg")
try:
    from mutagen.flac import FLAC

    _MUTAGEN_REAL = isinstance(FLAC, type)
except Exception:  # noqa: BLE001
    _MUTAGEN_REAL = False

reais = pytest.mark.skipif(
    not (FFMPEG and _MUTAGEN_REAL), reason="ffmpeg ou mutagen real indisponível"
)


def _audio(caminho, codec):
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            codec,
            str(caminho),
        ],
        check=True,
        capture_output=True,
    )


@reais
class TestComArquivosReais:
    def _flac(self, pasta, nome, artist, **extra):
        caminho = pasta / nome
        _audio(caminho, "flac")
        audio = FLAC(str(caminho))
        audio["ARTIST"] = artist
        for chave, valor in extra.items():
            audio[chave] = valor
        audio.save()
        return str(caminho)

    def test_le_lista_multivalorada_e_texto_unico_do_flac(self, tmp_path):
        a = self._flac(tmp_path, "a.flac", ["Tyler", "The Creator"])
        b = self._flac(tmp_path, "b.flac", "Tyler, The Creator, Kali Uchis")
        assert name_suggest.read_artist_lists(a) == [["Tyler", "The Creator"]]
        assert name_suggest.read_artist_lists(b) == [
            ["Tyler", "The Creator", "Kali Uchis"]
        ]

    def test_le_id3_do_mp3(self, tmp_path):
        import mutagen.id3 as id3

        caminho = tmp_path / "a.mp3"
        _audio(caminho, "libmp3lame")
        tags = id3.ID3()
        tags.add(id3.TPE1(encoding=3, text=["Tyler", "The Creator"]))
        tags.add(id3.TPE2(encoding=3, text=["Tyler, The Creator"]))
        tags.save(str(caminho), v2_version=4)
        assert name_suggest.read_artist_lists(str(caminho)) == [
            ["Tyler", "The Creator"],
            ["Tyler", "The Creator"],
        ]

    def test_arquivo_ilegivel_devolve_lista_vazia(self, tmp_path):
        ruim = tmp_path / "ruim.flac"
        ruim.write_bytes(b"nao sou flac")
        assert name_suggest.read_artist_lists(str(ruim)) == []

    def test_varredura_da_pasta_e_sugestao_ponta_a_ponta(self, tmp_path):
        for i in range(4):
            self._flac(
                tmp_path, f"f{i}.flac", ["Tyler", "The Creator", f"Convidado {i}"]
            )
        self._flac(tmp_path, "solo.flac", ["Outro Artista"])
        visto = []
        dados = name_suggest.scan_directory(
            str(tmp_path), lambda f, t: visto.append((f, t))
        )
        assert len(dados) == 5 and visto[-1] == (5, 5)
        assert name_suggest.suggest_from_lists(dados) == [
            {"name": "Tyler, The Creator", "files": 4}
        ]

    def test_comando_imprime_sugestao_e_linha_do_config(self, tmp_path, monkeypatch):
        from qobuz_dl import cli, ui

        for i in range(3):
            self._flac(tmp_path, f"f{i}.flac", "Tyler, The Creator")
            self._flac(tmp_path, f"g{i}.flac", ["Tyler", "The Creator"])
        saida = []
        monkeypatch.setattr(ui, "emit", lambda t="", *a, **k: saida.append(str(t)))
        monkeypatch.setattr(ui, "step", lambda t, *a, **k: saida.append(str(t)))
        # "Tyler, The Creator" já é protegido por padrão: não deve ser sugerido
        codigo = cli._handle_tags_suggest_names(str(tmp_path), QobuzDLSettings())
        assert codigo == 0
        assert not any("artists_with_comma" in linha for linha in saida)

    def test_comando_com_nome_novo_mostra_a_linha_do_config(
        self, tmp_path, monkeypatch
    ):
        from qobuz_dl import cli, ui

        for i in range(4):
            self._flac(tmp_path, f"f{i}.flac", ["Banda", "Do Fulano", f"Conv {i}"])
        saida = []
        monkeypatch.setattr(ui, "emit", lambda t="", *a, **k: saida.append(str(t)))
        monkeypatch.setattr(ui, "step", lambda t, *a, **k: saida.append(str(t)))
        assert cli._handle_tags_suggest_names(str(tmp_path), QobuzDLSettings()) == 0
        texto = "\n".join(saida)
        assert "Banda, Do Fulano" in texto
        assert "artists_with_comma = Banda, Do Fulano" in texto

    def test_pasta_inexistente_e_erro(self, tmp_path, monkeypatch):
        from qobuz_dl import cli, ui

        erros = []
        monkeypatch.setattr(ui, "error", lambda t, *a, **k: erros.append(str(t)))
        assert (
            cli._handle_tags_suggest_names(str(tmp_path / "nao"), QobuzDLSettings())
            == 1
        )
        assert erros
