"""Funções puras do layout compacto da lista interativa e a opção que o escolhe."""

import configparser

import pytest

from qobuz_dl import cli, config_cmd, ui
from qobuz_dl import interactive_ui as iu

pytestmark = pytest.mark.unit

ALBUM = {
    "title": "Kind of Blue (Legacy Edition) [Remastered 2009 with bonus tracks]",
    "artist": "Miles Davis",
    "type": "Album",
    "year": "1959",
    "quality": "24b/96kHz",
    "tracks_count": 9,
    "duration": "00:45:00",
}


def _linha(meta, categoria="album", colunas=40, prefix=" ➤ ", **kw):
    principal, sub, ano, qualidade = iu.compact_row_texts(
        meta, categoria, colunas, **kw
    )
    return iu.compact_fragments(prefix, principal, sub, ano, qualidade, colunas)


def _texto(fragmentos):
    return "".join(t for _k, t in fragmentos)


class TestResolveLayout:
    @pytest.mark.parametrize(
        "pref,cabe,colunas,esperado",
        [
            ("auto", True, 140, "tabela"),
            ("auto", False, 40, "compacto"),
            ("auto", False, 72, "compacto"),
            ("auto", False, 80, "cartao"),
            ("cartao", True, 140, "cartao"),
            ("cartao", False, 40, "cartao"),
            ("compacto", True, 140, "compacto"),
            ("compacto", False, 40, "compacto"),
            ("tabela", True, 140, "tabela"),
            ("tabela", False, 40, "compacto"),
            ("lixo", False, 40, "compacto"),  # inválido vale "auto"
            (None, False, 80, "cartao"),
        ],
    )
    def test_matriz(self, pref, cabe, colunas, esperado):
        assert iu.resolve_layout(pref, cabe, "album", colunas) == esperado

    @pytest.mark.parametrize("pref", iu.LAYOUTS)
    def test_menu_simples_sempre_cartao(self, pref):
        assert iu.resolve_layout(pref, False, "filter", 40) == "cartao"

    def test_layouts_vem_do_ui(self):
        assert iu.LAYOUTS == ui.LAYOUTS_INTERATIVOS


class TestFit:
    def test_cabe_volta_igual(self):
        assert iu._fit("abc", 5) == "abc"

    def test_corta_com_reticencias_na_largura_exata(self):
        saida = iu._fit("abcdefghij", 6)
        assert saida == "abcde…" and iu.get_cwidth(saida) == 6

    def test_caractere_largo_nao_estoura(self):
        assert iu.get_cwidth(iu._fit("日本語日本語", 7)) <= 7

    def test_larguras_minimas(self):
        assert iu._fit("abc", 0) == "" and iu._fit("abc", 1) == "…"


class TestLinhaCompacta:
    @pytest.mark.parametrize("colunas", [32, 40, 56, 72, 100])
    def test_nunca_passa_da_tela(self, colunas):
        linha = _texto(_linha(ALBUM, colunas=colunas))
        assert iu.get_cwidth(linha) <= colunas - 1

    def test_qualidade_fica_na_ponta_direita(self):
        linha = _texto(_linha(ALBUM, colunas=40))
        assert linha.endswith("24/96")

    def test_titulo_comprido_deixa_espaco_para_o_artista(self):
        linha = _texto(_linha(ALBUM, colunas=40))
        assert "Miles" in linha

    def test_ano_so_aparece_em_tela_larga(self):
        assert "1959" not in _texto(_linha(ALBUM, colunas=40))
        assert "1959" in _texto(_linha(ALBUM, colunas=60))

    def test_titulo_curto_nao_e_cortado(self):
        meta = dict(ALBUM, title="Blue Train")
        assert "Blue Train" in _texto(_linha(meta, colunas=40))
        assert "…" not in _texto(_linha(meta, colunas=40, prefix=" ➤ "))

    def test_tela_minuscula_so_o_titulo(self):
        linha = _texto(_linha(ALBUM, colunas=14, prefix=" ➤ * "))
        assert iu.get_cwidth(linha) <= 13

    def test_sem_qualidade_nem_artista(self):
        meta = {"title": "Só título"}
        assert "Só título" in _texto(_linha(meta, colunas=40))

    def test_lista_de_albuns_de_um_artista_mostra_funcao_e_ano(self):
        meta = dict(
            ALBUM, show_role=True, role="Principal", role_full="Artista Principal"
        )
        principal, sub, ano, _q = iu.compact_row_texts(
            meta, "album", 40, album_role=True
        )
        assert sub == "Principal" and ano == "1959"

    def test_playlist_e_artista(self):
        assert iu.compact_row_texts(
            {"name": "P", "owner": "Ana", "count": 20}, "playlist", 40
        ) == ("P", "Ana", "", "20 fx")
        assert iu.compact_row_texts({"name": "A", "count": 3}, "artist", 40) == (
            "A",
            "",
            "",
            "3 lanç.",
        )


class TestPainelDeDetalhes:
    @pytest.mark.parametrize("colunas", [32, 40, 72])
    def test_sempre_devolve_a_altura_fixa_e_cabe(self, colunas):
        linhas = iu.compact_detail_lines(ALBUM, "album", colunas)
        assert len(linhas) == iu.LINHAS_DETALHE
        assert all(iu.get_cwidth(t) <= colunas for _s, t in linhas)

    def test_titulo_inteiro_em_ate_duas_linhas(self):
        texto = " ".join(t for _s, t in iu.compact_detail_lines(ALBUM, "album", 40))
        assert "Kind of Blue" in texto and "bonus" in texto

    def test_dados_na_ordem_de_importancia_e_o_que_nao_cabe_sai(self):
        _s, dados = iu.compact_detail_lines(ALBUM, "album", 32)[2]
        assert "Miles Davis" in dados
        assert "45:00" not in dados  # o último dado é o primeiro a sair

    def test_sem_meta_devolve_linhas_vazias(self):
        linhas = iu.compact_detail_lines(None, "album", 40)
        assert [t for _s, t in linhas] == ["", "", ""]

    def test_faixa_mostra_o_album(self):
        meta = {"title": "Faixa", "artist": "A", "album": "Disco X", "year": "2020"}
        _s, dados = iu.compact_detail_lines(meta, "track", 60)[1]
        assert "Disco X" in dados

    def test_titulo_muito_longo_termina_com_reticencias(self):
        meta = dict(ALBUM, title="palavra " * 40)
        linhas = [t for _s, t in iu.compact_detail_lines(meta, "album", 32)]
        assert linhas[1].endswith("…")


class TestEstadoELeitura:
    def test_configure_aceita_e_rejeita(self):
        with ui.estado_temporario():
            ui.configure(layout_interativo="compacto")
            assert ui.estado.layout_interativo == "compacto"
            with pytest.raises(ValueError):
                ui.configure(layout_interativo="holograma")
            assert ui.estado.layout_interativo == "compacto"

    @staticmethod
    def _config(**extra):
        config = configparser.ConfigParser(interpolation=None)
        config.add_section("qobuz")
        base = {
            "email": "a@b.c",
            "default_limit": "20",
            "default_quality": "6",
            "app_id": "1",
            "secrets": "a",
            "disable_keyring": "true",
        }
        base.update(extra)
        for k, v in base.items():
            config.set("qobuz", k, v)
        return config

    def test_padrao_quando_a_chave_nao_existe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        assert cli._load_cli_config(self._config(), 0.0).interactive_layout == "auto"

    def test_valor_e_normalizado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        cfg = cli._load_cli_config(self._config(interactive_layout=" Compacto "), 0.0)
        assert cfg.interactive_layout == "compacto"

    def test_valor_invalido_avisa_e_vira_auto(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        cfg = cli._load_cli_config(self._config(interactive_layout="holograma"), 0.0)
        assert cfg.interactive_layout == "auto"
        assert "interactive_layout" in capsys.readouterr().out


class TestConfigSet:
    def _rodar(self, tmp_path, texto, valor):
        from types import SimpleNamespace

        arquivo = tmp_path / "config.ini"
        arquivo.write_text(texto, encoding="utf-8")
        args = SimpleNamespace(
            action="set", key="interactive_layout", value=valor, nova=False, json=False
        )
        codigo = config_cmd.cmd_config(
            args, config_file=str(arquivo), db_path=str(tmp_path / "db")
        )
        return codigo, arquivo.read_text(encoding="utf-8")

    def test_cria_a_chave_sozinho_em_config_antigo(self, tmp_path):
        codigo, texto = self._rodar(
            tmp_path, "[qobuz]\ndefault_limit = 20\n", "compacto"
        )
        assert codigo == 0 and "interactive_layout = compacto" in texto

    def test_troca_o_valor_existente(self, tmp_path):
        codigo, texto = self._rodar(
            tmp_path, "[qobuz]\ninteractive_layout = auto\n", "cartao"
        )
        assert codigo == 0 and "interactive_layout = cartao" in texto

    def test_valor_invalido_nao_grava(self, tmp_path):
        original = "[qobuz]\ninteractive_layout = auto\n"
        codigo, texto = self._rodar(tmp_path, original, "holograma")
        assert codigo == 1 and texto == original
