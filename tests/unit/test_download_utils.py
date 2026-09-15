"""Testa qobuz_dl/download_utils.py -- funções de apoio ao download que
ainda não tinham arquivo dedicado (95.51% via cobertura indireta de
outros testes, mas com 4 linhas/branches nunca exercitados diretamente).

Duas frentes:
  1. process_folder_format_with_subdirs() -- monta o caminho de pastas a
     partir de um template tipo "{artista}/{album}", tratando tanto uma
     parte que "sanitiza pra vazio" (queda no `if cleaned_part:`) quanto
     uma chave de formato que não existe no dicionário (KeyError).
  2. _artist_label() -- usa get_album_artist() por baixo; cobre o
     `except Exception` quando essa chamada explode.

NOTA sobre um branch que ficou de fora de propósito: existe um segundo
`if cleaned_part:` (linha 176) dentro do bloco `except KeyError`, cujo
ramo "falso" exigiria uma `part` que ao mesmo tempo (a) contenha um
`{campo_inexistente}` pra disparar o KeyError, e (b) sanitize para uma
string vazia. Como clean_filename() só remove separadores/pontuação e
pares de colchetes SEM texto dentro, e qualquer nome de campo válido em
`.format()` tem que ter pelo menos um caractere alfanumérico, o texto
literal do campo sempre sobrevive à limpeza -- não achei uma entrada
real que force esse ramo (parece código morto/defensivo). Preferi
sinalizar isso a forçar um teste que não prova nada.
"""

import pytest

from qobuz_dl import download_utils as du

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------
# process_folder_format_with_subdirs
# ---------------------------------------------------------------------
class TestProcessFolderFormatComSubdirs:
    def test_parte_que_sanitiza_para_vazio_e_ignorada(self):
        # "   " não tem chave de formato nenhuma (sem KeyError) mas vira
        # "" depois de clean_filename -- tem que sumir do caminho final,
        # sem deixar uma barra dupla ou pasta vazia no meio.
        resultado = du.process_folder_format_with_subdirs(
            "normal/   /outro", {}
        )
        assert resultado == "normal/outro"

    def test_chave_de_formato_inexistente_cai_no_except_e_usa_texto_original(self):
        resultado = du.process_folder_format_with_subdirs(
            "{artista}/{nao_existe}", {"artista": "Artista X"}
        )
        # a parte com a chave inexistente usa o texto ORIGINAL (com a
        # chave ainda entre chaves), não uma string vazia -- o programa
        # não trava, só avisa e segue com o literal.
        assert resultado == "Artista X/{nao_existe}"

    def test_texto_original_muito_longo_no_except_tambem_e_truncado(self):
        # Mesma regra de truncamento (>120 chars vira "...") tem que
        # valer tanto no caminho feliz quanto no fallback do except --
        # antes deste teste, só o caminho feliz (linhas 158-161) tinha
        # cobertura; o equivalente dentro do except (172-175) não.
        parte_longa = "{nao_existe}" + "a" * 150
        resultado = du.process_folder_format_with_subdirs(parte_longa, {})

        assert "..." in resultado
        assert len(resultado) < len(parte_longa)


# ---------------------------------------------------------------------
# _artist_label
# ---------------------------------------------------------------------
class TestArtistLabel:
    def test_get_album_artist_explodindo_cai_no_fallback(self, monkeypatch):
        def _explode(item_dict):
            raise RuntimeError("erro inesperado de metadata")

        monkeypatch.setattr(du, "get_album_artist", _explode)

        assert du._artist_label({"qualquer": 1}, fallback="Fallback X") == "Fallback X"
