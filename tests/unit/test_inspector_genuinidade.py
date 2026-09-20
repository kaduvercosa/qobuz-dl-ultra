"""Testa só os "guard clauses" (saídas antecipadas) de
`_checar_genuinidade()` em qobuz_dl/inspector.py -- as condições que
decidem se a análise espectral roda ou não. A análise em si (decodificar
áudio via ffmpeg + FFT com numpy) fica de fora de propósito: exigiria
arquivos de áudio reais e não dá pra verificar o resultado numérico sem
rodar de verdade, o que não é seguro fazer às cegas num teste que eu não
consigo executar aqui.

`numpy` é FAKEADO via `sys.modules` em todos os testes (mesmo nos que
"têm" numpy disponível) -- assim os testes não dependem de numpy estar
de fato instalado no ambiente que roda a suíte; só a ausência DELE no
sys.modules (forçada com `sys.modules["numpy"] = None`) importa pro
primeiro teste.
"""

import sys

import pytest

from qobuz_dl import inspector

pytestmark = pytest.mark.unit


@pytest.fixture
def numpy_fake(monkeypatch):
    """Garante que `import numpy` funcione sem precisar do pacote de
    verdade instalado -- a função só faz o import, não usa nada dele
    antes dos guard clauses que estes testes checam."""
    monkeypatch.setitem(sys.modules, "numpy", type(sys)("numpy_fake"))


class TestGuardClauses:
    def test_sem_numpy_devolve_indisponivel_com_motivo(self, monkeypatch):
        # None em sys.modules força ImportError mesmo se numpy estiver
        # de fato instalado no ambiente que roda a suíte.
        monkeypatch.setitem(sys.modules, "numpy", None)

        resultado = inspector._checar_genuinidade("faixa.flac", 44100, 200)

        assert resultado["disponivel"] is False
        assert "numpy" in resultado["motivo"].lower()

    def test_sem_ffmpeg_devolve_indisponivel_com_motivo(self, numpy_fake, monkeypatch):
        monkeypatch.setattr(inspector, "encontrar_binario", lambda nome: None)

        resultado = inspector._checar_genuinidade("faixa.flac", 44100, 200)

        assert resultado["disponivel"] is False
        assert "ffmpeg" in resultado["motivo"].lower()

    @pytest.mark.parametrize(
        "sample_rate,duracao_s",
        [
            (None, 200),
            (44100, None),
            (44100, 0),
            (44100, 2.9),  # abaixo do mínimo de 3s
        ],
    )
    def test_dados_insuficientes_devolve_indisponivel(
        self, numpy_fake, monkeypatch, sample_rate, duracao_s
    ):
        monkeypatch.setattr(
            inspector, "encontrar_binario", lambda nome: "/usr/bin/ffmpeg"
        )

        resultado = inspector._checar_genuinidade("faixa.flac", sample_rate, duracao_s)

        assert resultado["disponivel"] is False
