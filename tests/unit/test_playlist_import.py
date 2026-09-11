"""Testa qobuz_dl/playlist_import.py: converte playlists exportadas de
qualquer plataforma (TXT, CSV, JSON de Spotify/Last.fm/Exportify/Soundiiz/
etc.) numa lista normalizada de {"artist", "title"} pro pipeline de fuzzy
matching + download.

Escrito direto contra arquivos reais em `tmp_path` (não contra as funções
privadas `_parse_*` isoladas) porque é assim que o módulo é usado de
verdade -- `parse_playlist_file()` é o único ponto de entrada público, e
testar por fora garante que a detecção de formato por extensão/conteúdo
também está coberta, não só o parsing em si.
"""

import json

import pytest

from qobuz_dl.playlist_import import parse_playlist_file

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Arquivo inexistente / formato genérico
# ---------------------------------------------------------------------------
class TestArquivoInexistenteOuVazio:
    def test_arquivo_inexistente_levanta_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_playlist_file(str(tmp_path / "nao-existe.txt"))

    def test_txt_vazio_levanta_valueerror(self, tmp_path):
        arquivo = tmp_path / "vazia.txt"
        arquivo.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_playlist_file(str(arquivo))

    def test_txt_so_com_comentarios_levanta_valueerror(self, tmp_path):
        arquivo = tmp_path / "so_comentarios.txt"
        arquivo.write_text(
            "# playlist exportada em 2024\n# 12 faixas\n", encoding="utf-8"
        )
        with pytest.raises(ValueError):
            parse_playlist_file(str(arquivo))


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------
class TestParseTxt:
    def test_separador_hifen(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Radiohead - Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_separador_dois_pontos(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Radiohead: Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_separador_pipe(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Radiohead | Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_separador_en_dash(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Radiohead \u2013 Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_dois_pontos_sem_espaco_depois_nao_e_separador(self, tmp_path):
        """ "Artista:Título" sem espaço após os dois-pontos não bate o
        regex de separador -- vira tudo título, sem artista. Comportamento
        real do `_SEP_RE`, travado aqui de propósito."""
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Radiohead:Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "", "title": "Radiohead:Creep"}
        ]

    def test_linha_sem_separador_vira_so_titulo(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Bohemian Rhapsody\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "", "title": "Bohemian Rhapsody"}
        ]

    def test_so_o_primeiro_hifen_separa_titulos_com_hifen(self, tmp_path):
        """maxsplit=1 -- um título que também tem " - " no meio (comum em
        faixas tipo "Álbum - Faixa - Remix") não deve ser cortado ao
        meio."""
        arquivo = tmp_path / "p.txt"
        arquivo.write_text("Sigur Rós - Untitled - Vaka\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Sigur Rós", "title": "Untitled - Vaka"}
        ]

    def test_linhas_em_branco_e_comentarios_sao_ignorados(self, tmp_path):
        arquivo = tmp_path / "p.txt"
        arquivo.write_text(
            "# minha playlist\n"
            "\n"
            "Radiohead - Creep\n"
            "   \n"
            "# fim\n"
            "Pixies - Where Is My Mind?\n",
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"},
            {"artist": "Pixies", "title": "Where Is My Mind?"},
        ]

    def test_extensao_text_tambem_e_aceita_como_txt(self, tmp_path):
        arquivo = tmp_path / "p.text"
        arquivo.write_text("Radiohead - Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_sem_extensao_com_conteudo_de_texto_puro_cai_no_parser_txt(self, tmp_path):
        arquivo = tmp_path / "playlist_sem_extensao"
        arquivo.write_text("Radiohead - Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
class TestParseCsv:
    def test_colunas_artist_e_title_basicas(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text(
            "artist,title\nRadiohead,Creep\nPixies,Debaser\n", encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"},
            {"artist": "Pixies", "title": "Debaser"},
        ]

    def test_deteccao_de_coluna_case_insensitive_e_sinonimos(self, tmp_path):
        """Exportify usa "Artist Name(s)"/"Track Name"; outros exports
        usam "Track"/"Song"/"Performer" etc. -- tudo tem que mapear."""
        arquivo = tmp_path / "p.csv"
        arquivo.write_text("Performer,Song Name\nRadiohead,Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_separador_ponto_e_virgula_detectado_automaticamente(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text("artist;title\nRadiohead;Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_separador_tab_detectado_automaticamente(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text("artist\ttitle\nRadiohead\tCreep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_multiplos_artistas_pega_so_o_primeiro(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text(
            'artist,title\n"Artista A, Artista B",Faixa\n', encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Artista A", "title": "Faixa"}
        ]

    def test_linha_sem_titulo_e_descartada_silenciosamente(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text(
            "artist,title\nRadiohead,Creep\nSem Titulo,\n", encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_sem_coluna_de_titulo_reconhecida_levanta_valueerror_explicativo(
        self, tmp_path
    ):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text("coluna_a,coluna_b\nx,y\n", encoding="utf-8")
        with pytest.raises(ValueError, match="coluna_a"):
            parse_playlist_file(str(arquivo))

    def test_sem_coluna_de_artista_ainda_funciona_so_com_titulo(self, tmp_path):
        arquivo = tmp_path / "p.csv"
        arquivo.write_text("title\nCreep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [{"artist": "", "title": "Creep"}]


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
class TestParseJson:
    def test_formato_generico_flat(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps([{"artist": "Radiohead", "title": "Creep"}]), encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_formato_generico_com_name_em_vez_de_title(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps([{"artist": "Radiohead", "name": "Creep"}]), encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_formato_generico_com_lista_de_artistas(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps([{"artists": ["Radiohead", "Outro"], "title": "Creep"}]),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_exportify(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps([{"Track Name": "Creep", "Artist Name(s)": "Radiohead, Outro"}]),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_spotify_api_com_items(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "track": {
                                "name": "Creep",
                                "artists": [{"name": "Radiohead"}],
                            }
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_lastfm_com_wrapper_track(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps({"track": [{"name": "Creep", "artist": {"name": "Radiohead"}}]}),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_lastfm_artist_como_string_direta(self, tmp_path):
        """Alguns exports do Last.fm mandam "artist" como string em vez
        de {"name": ...} -- o código trata os dois casos."""
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps([{"name": "Creep", "artist": "Radiohead"}]), encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_objeto_unico_sem_wrapper_vira_lista_de_um(self, tmp_path):
        """Um JSON de nível superior que é um único dict (não uma lista,
        nem tem "items"/"track") é tratado como uma playlist de 1 faixa."""
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps({"artist": "Radiohead", "title": "Creep"}), encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_item_sem_titulo_e_descartado(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps(
                [
                    {"artist": "Radiohead", "title": "Creep"},
                    {"artist": "Sem Titulo"},
                ]
            ),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_item_que_nao_e_dict_e_ignorado_sem_quebrar(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(
            json.dumps(
                ["isso não é uma faixa", {"artist": "Radiohead", "title": "Creep"}]
            ),
            encoding="utf-8",
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_json_de_nivel_superior_que_nao_e_lista_nem_dict_de_faixa(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text(json.dumps(42), encoding="utf-8")
        with pytest.raises(ValueError, match="lista de faixas"):
            parse_playlist_file(str(arquivo))

    def test_lista_vazia_levanta_valueerror(self, tmp_path):
        arquivo = tmp_path / "p.json"
        arquivo.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_playlist_file(str(arquivo))


# ---------------------------------------------------------------------------
# Detecção de formato pelo conteúdo (arquivos sem extensão reconhecida)
# ---------------------------------------------------------------------------
class TestDeteccaoPorConteudo:
    def test_conteudo_json_sem_extensao_json(self, tmp_path):
        arquivo = tmp_path / "playlist.dat"
        arquivo.write_text(
            json.dumps([{"artist": "Radiohead", "title": "Creep"}]), encoding="utf-8"
        )
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]

    def test_conteudo_csv_sem_extensao_csv(self, tmp_path):
        arquivo = tmp_path / "playlist.dat"
        arquivo.write_text("artist,title\nRadiohead,Creep\n", encoding="utf-8")
        assert parse_playlist_file(str(arquivo)) == [
            {"artist": "Radiohead", "title": "Creep"}
        ]
