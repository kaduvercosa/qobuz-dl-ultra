"""Testa `_suavizar()` e `_gerar_grafico_html()` de qobuz_dl/inspector.py.

`_gerar_grafico_html` em si é só formatação de string + aritmética
determinística (sem I/O externo além de escrever o .html final, sem
número aleatório) -- por isso dá pra testar o caminho feliz com
segurança, diferente da heurística de FFT (`_checar_genuinidade`) que
fica de fora por depender de numpy/ffmpeg decodificando áudio real.

Os testes do caminho feliz checam só ESTRUTURA (arquivo certo escrito,
manchete/rótulo do veredito presentes) -- não tentam validar as
coordenadas SVG geradas, isso seria overfitting num detalhe visual que
não muda o comportamento que importa.
"""

import pytest

from qobuz_dl import inspector

pytestmark = pytest.mark.unit


class TestSuavizar:
    def test_media_movel_simples(self):
        resultado = inspector._suavizar([1, 2, 3, 4, 5], janela=3)
        # cada ponto vira a média dele com o vizinho de cada lado
        assert resultado == pytest.approx([1.5, 2.0, 3.0, 4.0, 4.5])

    def test_janela_1_ou_menor_devolve_lista_sem_alterar(self):
        assert inspector._suavizar([1, 2, 3], janela=1) == [1, 2, 3]
        assert inspector._suavizar([1, 2, 3], janela=0) == [1, 2, 3]

    def test_lista_vazia_devolve_lista_vazia(self):
        assert inspector._suavizar([], janela=5) == []

    def test_devolve_lista_nao_o_iteravel_original(self):
        resultado = inspector._suavizar((1, 2, 3), janela=1)
        assert isinstance(resultado, list)


def _genuinidade(veredito="genuino", **overrides):
    base = {
        "disponivel": True,
        "freqs_hz": [0, 5000, 10000, 15000, 20000, 22000],
        "media_db": [-10.0, -12.0, -15.0, -20.0, -60.0, -65.0],
        "nyquist_hz": 22050,
        "corte_hz": 20000,
        "piso_db": -65.0,
        "pico_db": -10.0,
        "veredito": veredito,
        "proporcao": 0.9,
    }
    base.update(overrides)
    return base


class TestGerarGraficoHtml:
    def test_indisponivel_devolve_none_sem_escrever_arquivo(self, tmp_path):
        audio = str(tmp_path / "Faixa.flac")
        resultado = inspector._gerar_grafico_html(
            audio, _genuinidade(disponivel=False)
        )

        assert resultado is None
        assert list(tmp_path.glob("*-spec.html")) == []

    def test_sem_freqs_hz_devolve_none_sem_escrever_arquivo(self, tmp_path):
        audio = str(tmp_path / "Faixa.flac")
        resultado = inspector._gerar_grafico_html(
            audio, _genuinidade(freqs_hz=[])
        )

        assert resultado is None
        assert list(tmp_path.glob("*-spec.html")) == []

    def test_caminho_feliz_escreve_html_com_nome_e_manchete_certos(self, tmp_path):
        audio = str(tmp_path / "Minha Faixa.flac")

        destino = inspector._gerar_grafico_html(audio, _genuinidade("suspeito"))

        assert destino == str(tmp_path / "Minha Faixa-spec.html")
        conteudo = open(destino, encoding="utf-8").read()
        assert "MINHA FAIXA" in conteudo
        assert "SUSPEITO" in conteudo
        assert "provavelmente N" in conteudo  # trecho da manchete de "suspeito"

    @pytest.mark.parametrize(
        "veredito,marca_esperada",
        [
            ("genuino", "GENUÍNO"),
            ("inconclusivo", "INCONCLUSIVO"),
            ("suspeito", "SUSPEITO"),
        ],
    )
    def test_badge_bate_com_o_veredito(self, tmp_path, veredito, marca_esperada):
        audio = str(tmp_path / "Outra.flac")

        destino = inspector._gerar_grafico_html(audio, _genuinidade(veredito))

        conteudo = open(destino, encoding="utf-8").read()
        assert f'class="badge badge-{veredito}"' in conteudo
        assert marca_esperada in conteudo

    def test_veredito_desconhecido_lanca_key_error(self, tmp_path):
        """Documentando o comportamento atual: um veredito fora dos três
        conhecidos ("genuino"/"inconclusivo"/"suspeito") não cai num
        fallback -- estoura KeyError no lookup do dict de manchetes.
        Só chega aqui se `_checar_genuinidade` algum dia computar um
        veredito novo sem atualizar esta função junto."""
        audio = str(tmp_path / "Faixa.flac")

        with pytest.raises(KeyError):
            inspector._gerar_grafico_html(audio, _genuinidade("desconhecido"))
