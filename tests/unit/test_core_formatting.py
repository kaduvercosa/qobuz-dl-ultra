"""Testa qobuz_dl/core.py -- só o recorte de lógica pura/isolada:
`_shade` (derivação de tons da cor de destaque), `_align_text` (padding e
truncamento com largura visual correta) e `QobuzDL.mark_url_done_in_file`
(marcação de progresso em arquivo .txt).

FORA DE ESCOPO DE PROPÓSITO
-----------------------------
`interactive()`, `handle_url()`, `download_from_id()` e o resto da
orquestração de download/UI interativa de core.py (a maior parte das
1900 linhas do arquivo) não entra aqui -- são métodos gigantes que
misturam entrada de teclado, chamadas de rede via qopy.Client, e I/O de
arquivo real. Testar eles de verdade exigiria um nível de mock tão
grande que o teste vira mais frágil que o código, e não é um recorte
que dá pra fazer com confiança numa passada só. Cobrir isso é trabalho
futuro, feito em pedaços menores.
"""

from qobuz_dl import core


# --------------------------------------------------------------------
# _shade
# --------------------------------------------------------------------
class TestShade:
    def test_fator_zero_devolve_a_cor_de_destaque_original(self):
        # f=0 não deveria misturar nada -- a cor sai igual à base.
        assert core._shade(0) == core._hex_accent

    def test_fator_positivo_maximo_vai_pro_branco(self):
        assert core._shade(1.0) == "#ffffff"

    def test_fator_negativo_maximo_vai_pro_preto(self):
        assert core._shade(-1.0) == "#000000"

    def test_fator_positivo_fica_entre_a_base_e_o_branco(self):
        # Não testa um valor hardcoded (a cor de destaque pode vir do
        # config.ini do usuário) -- testa a RELAÇÃO: clarear um pouco
        # tem que ficar estritamente entre a cor original e o branco,
        # canal a canal.
        r0, g0, b0 = _hex_to_rgb(core._shade(0))
        r1, g1, b1 = _hex_to_rgb(core._shade(0.5))
        assert r0 <= r1 <= 255
        assert g0 <= g1 <= 255
        assert b0 <= b1 <= 255

    def test_fator_negativo_fica_entre_a_base_e_o_preto(self):
        r0, g0, b0 = _hex_to_rgb(core._shade(0))
        r1, g1, b1 = _hex_to_rgb(core._shade(-0.5))
        assert 0 <= r1 <= r0
        assert 0 <= g1 <= g0
        assert 0 <= b1 <= b0

    def test_formato_e_sempre_hex_de_6_digitos(self):
        for f in (0, 0.2, -0.2, 0.3, -0.3):
            resultado = core._shade(f)
            assert resultado.startswith("#")
            assert len(resultado) == 7  # "#" + 6 hex digits

    def test_fator_fora_do_intervalo_esperado_nao_gera_hex_malformado(self):
        # Regressão: sem o clamp, um fator > 1 produzia canal > 255, e
        # int > 255 formatado com "02x" vira 3+ dígitos hex em vez de 2
        # (ex.: 415 -> "19f"), quebrando o formato "#RRGGBB".
        resultado = core._shade(5.0)
        assert len(resultado) == 7
        assert resultado == "#ffffff"  # ainda clampado no branco

        resultado = core._shade(-5.0)
        assert len(resultado) == 7
        assert resultado == "#000000"  # ainda clampado no preto


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------
# _align_text
# --------------------------------------------------------------------
class TestAlignText:
    def test_texto_menor_que_a_largura_e_preenchido_com_espacos(self):
        assert core._align_text("abc", 10) == "abc" + " " * 7

    def test_texto_do_tamanho_exato_nao_muda(self):
        assert core._align_text("abcde", 5) == "abcde"

    def test_texto_maior_e_truncado_com_reticencias(self):
        resultado = core._align_text("Um título de faixa bem comprido", 10)
        assert len(resultado) <= 10
        assert resultado.endswith("...")

    def test_none_vira_string_vazia_preenchida(self):
        assert core._align_text(None, 5) == "     "

    def test_numero_e_convertido_pra_string(self):
        assert core._align_text(42, 5) == "42   "

    def test_largura_zero_ou_negativa_nao_quebra(self):
        # width - 3 fica negativo aqui -- a função tem que devolver algo
        # sem lançar exceção, não travar a tabela inteira por causa de
        # uma coluna configurada errado.
        resultado = core._align_text("qualquer coisa", 0)
        assert isinstance(resultado, str)

    def test_emoji_conta_como_largura_dupla(self):
        # get_cwidth trata a maioria dos emojis como largura visual 2 (não
        # 1 caractere = 1 coluna) -- um emoji sozinho já ocupa metade de
        # uma largura de 4, então o padding tem que compensar por isso,
        # não pelo len() bruto da string.
        resultado = core._align_text("🎵", 4)
        assert len(resultado) == 3  # 1 char de emoji + 2 espaços de padding
        assert resultado.startswith("🎵")


# --------------------------------------------------------------------
# QobuzDL.mark_url_done_in_file
# --------------------------------------------------------------------
class TestMarkUrlDoneInFile:
    def _instancia(self, tmp_path):
        return core.QobuzDL(directory=str(tmp_path / "downloads"))

    def test_linha_correspondente_e_marcada_done(self, tmp_path):
        arquivo = tmp_path / "urls.txt"
        arquivo.write_text(
            "https://qobuz.com/album/1\nhttps://qobuz.com/album/2\n",
            encoding="utf-8",
        )
        dl = self._instancia(tmp_path)

        dl.mark_url_done_in_file(str(arquivo), "https://qobuz.com/album/1")

        linhas = arquivo.read_text(encoding="utf-8").splitlines()
        assert linhas[0] == "https://qobuz.com/album/1 [DONE]"
        assert linhas[1] == "https://qobuz.com/album/2"

    def test_espacos_ao_redor_da_url_sao_ignorados_na_comparacao(self, tmp_path):
        arquivo = tmp_path / "urls.txt"
        arquivo.write_text("  https://qobuz.com/album/1  \n", encoding="utf-8")
        dl = self._instancia(tmp_path)

        dl.mark_url_done_in_file(str(arquivo), "https://qobuz.com/album/1")

        assert "[DONE]" in arquivo.read_text(encoding="utf-8")

    def test_marcar_de_novo_nao_duplica_a_tag(self, tmp_path):
        # Cenário real: processo interrompido e rodado de novo -- uma URL
        # já marcada [DONE] não pode ganhar [DONE] [DONE] na segunda
        # passada (embora quem normalmente evite reprocessar seja o banco
        # de downloads, não esse marcador).
        arquivo = tmp_path / "urls.txt"
        arquivo.write_text("https://qobuz.com/album/1 [DONE]\n", encoding="utf-8")
        dl = self._instancia(tmp_path)

        dl.mark_url_done_in_file(str(arquivo), "https://qobuz.com/album/1")

        conteudo = arquivo.read_text(encoding="utf-8")
        assert conteudo.count("[DONE]") == 1
        # A comparação usa .strip() na linha inteira, então
        # "...1 [DONE]" != "...1" -- não bate de novo, e a linha original
        # com [DONE] é reescrita sem alteração (vai pro `else: f.write(line)`).

    def test_url_nao_encontrada_nao_altera_o_arquivo(self, tmp_path):
        arquivo = tmp_path / "urls.txt"
        conteudo_original = "https://qobuz.com/album/1\n"
        arquivo.write_text(conteudo_original, encoding="utf-8")
        dl = self._instancia(tmp_path)

        dl.mark_url_done_in_file(str(arquivo), "https://qobuz.com/album/999")

        assert arquivo.read_text(encoding="utf-8") == conteudo_original

    def test_arquivo_inexistente_nao_lanca_excecao(self, tmp_path):
        dl = self._instancia(tmp_path)
        # Não deve lançar -- só sai em silêncio (guard no início da função).
        dl.mark_url_done_in_file(str(tmp_path / "nao-existe.txt"), "qualquer-url")

    def test_txt_file_none_nao_lanca_excecao(self, tmp_path):
        dl = self._instancia(tmp_path)
        dl.mark_url_done_in_file(None, "qualquer-url")

    def test_apenas_a_linha_certa_e_marcada_com_urls_repetidas_por_engano(
        self, tmp_path
    ):
        # Se a mesma URL aparecer duas vezes no arquivo (usuário colou
        # duas vezes), as DUAS ficam marcadas -- não só a primeira. Não é
        # um bug: o comportamento documentado é "marca a(s) linha(s)
        # correspondente(s)", sem noção de índice/posição.
        arquivo = tmp_path / "urls.txt"
        arquivo.write_text(
            "https://qobuz.com/album/1\nhttps://qobuz.com/album/1\n",
            encoding="utf-8",
        )
        dl = self._instancia(tmp_path)

        dl.mark_url_done_in_file(str(arquivo), "https://qobuz.com/album/1")

        linhas = arquivo.read_text(encoding="utf-8").splitlines()
        assert linhas == [
            "https://qobuz.com/album/1 [DONE]",
            "https://qobuz.com/album/1 [DONE]",
        ]
