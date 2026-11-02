"""Testa `_mostrar_relatorio()` de qobuz_dl/inspector.py -- a função que
decide QUAIS painéis e mensagens aparecem, dado o relatório de tags e o
resultado da checagem de genuinidade. `_gerar_grafico_html` é substituída por
um stub em todo teste aqui -- ela já tem sua própria suíte em
test_inspector_grafico.py, não faz sentido duplicar; aqui o que importa é o
DESPACHO de UI ao redor dela (sucesso / sem dados / exceção).

Verificação por captura: `ui.emit` vira uma função que só guarda o texto
(cada painel sai numa única escrita), sem tocar o terminal de verdade. O texto
é comparado sem cores e com os espaços normalizados, para não depender da
largura da janela de quem roda a suíte.
"""

import re

import pytest

from qobuz_dl import inspector, ui

pytestmark = pytest.mark.unit

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _Saida:
    def __init__(self):
        self.blocos = []

    @property
    def texto(self):
        """Tudo o que foi impresso, sem cores, bordas nem espaços repetidos."""
        bruto = _ANSI.sub("", "\n".join(self.blocos))
        sem_borda = re.sub(r"[│╭╮╰╯─|+]", " ", bruto)
        return " ".join(sem_borda.split())

    def titulos(self):
        return re.findall(r"╭─ (.+?) ─+╮", _ANSI.sub("", "\n".join(self.blocos)))


@pytest.fixture
def saida(monkeypatch):
    captura = _Saida()
    monkeypatch.setattr(
        ui, "emit", lambda texto="", end="\n": captura.blocos.append(texto)
    )
    monkeypatch.setattr(ui, "raw_width", lambda *a, **k: 100)
    return captura


def _dados(**overrides):
    base = {
        "tecnico": {"Arquivo": "faixa.flac"},
        "tags": {"ARTIST": "Artista Teste"},
        "capas": [],
    }
    base.update(overrides)
    return base


def _genuinidade_disponivel(**overrides):
    base = {
        "disponivel": True,
        "cor": "ok",
        "mensagem": "Parece genuíno",
        "corte_hz": 20000,
        "nyquist_hz": 22050,
        "proporcao": 0.9,
    }
    base.update(overrides)
    return base


class TestMostrarRelatorio:
    def test_indisponivel_mostra_aviso_sem_gerar_grafico(
        self, monkeypatch, saida, tmp_path
    ):
        chamado_grafico = []
        monkeypatch.setattr(
            inspector,
            "_gerar_grafico_html",
            lambda *a, **k: chamado_grafico.append(True),
        )

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            {"disponivel": False, "motivo": "sem numpy instalado"},
        )

        assert "sem numpy instalado" in saida.texto
        assert chamado_grafico == []

    def test_cor_ok_mostra_o_veredito_e_avisa_grafico_salvo(
        self, monkeypatch, saida, tmp_path
    ):
        destino_fake = str(tmp_path / "faixa-spec.html")
        monkeypatch.setattr(
            inspector, "_gerar_grafico_html", lambda caminho, gen: destino_fake
        )

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            _genuinidade_disponivel(cor="ok", mensagem="Parece genuíno"),
        )

        assert "Parece genuíno" in saida.texto
        assert "Gráfico salvo em" in saida.texto
        assert destino_fake.replace("/", "") in saida.texto.replace("/", "").replace(
            " ", ""
        )

    def test_cor_warn_mostra_o_veredito(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            _genuinidade_disponivel(cor="warn", mensagem="Inconclusivo"),
        )

        assert "Inconclusivo" in saida.texto

    def test_cor_error_mostra_o_veredito(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            _genuinidade_disponivel(cor="error", mensagem="Provavelmente upsample"),
        )

        assert "Provavelmente upsample" in saida.texto

    def test_cada_cor_usa_o_glifo_do_seu_tom(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)
        glifos = {}
        for cor in ("ok", "warn", "error"):
            saida.blocos.clear()
            inspector._mostrar_relatorio(
                str(tmp_path / "faixa.flac"),
                _dados(),
                _genuinidade_disponivel(cor=cor, mensagem=f"veredito-{cor}"),
            )
            linha = next(
                l
                for l in _ANSI.sub("", "\n".join(saida.blocos)).splitlines()
                if f"veredito-{cor}" in l
            )
            glifos[cor] = linha.split("│")[1].split()[0]
        fancy_ou_plain = {
            tom: (ui._TONES[tom][0], ui._TONES[tom][1]) for tom in ("ok", "warn", "err")
        }
        assert glifos["ok"] in fancy_ou_plain["ok"]
        assert glifos["warn"] in fancy_ou_plain["warn"]
        assert glifos["error"] in fancy_ou_plain["err"]

    def test_grafico_none_avisa_dados_insuficientes(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"), _dados(), _genuinidade_disponivel()
        )

        assert "dados de espectro suficientes" in saida.texto

    def test_grafico_lanca_excecao_vira_erro_no_painel_sem_propagar(
        self, monkeypatch, saida, tmp_path
    ):
        def _explode(caminho, gen):
            raise RuntimeError("disco cheio")

        monkeypatch.setattr(inspector, "_gerar_grafico_html", _explode)

        # Não pode propagar -- _mostrar_relatorio mostra o erro e segue, não
        # derruba o resto de run_inspector().
        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"), _dados(), _genuinidade_disponivel()
        )

        assert "Erro ao gerar o gráfico: disco cheio" in saida.texto

    def test_sem_tags_mostra_mensagem_de_nenhuma_tag(
        self, monkeypatch, saida, tmp_path
    ):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(tags={}),
            {"disponivel": False, "motivo": "x"},
        )

        assert "nenhuma tag encontrada" in saida.texto

    def test_com_capas_mostra_painel_de_capas(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(capas=["[1] image/jpeg, 500x500, 200 KB"]),
            {"disponivel": False, "motivo": "x"},
        )

        assert "image/jpeg" in saida.texto
        assert any("CAPAS EMBUTIDAS (1)" in t for t in saida.titulos())

    def test_sem_capas_nao_mostra_painel_de_capas(self, monkeypatch, saida, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(capas=[]),
            {"disponivel": False, "motivo": "x"},
        )

        assert not any("CAPAS" in t for t in saida.titulos())

    def test_paineis_saem_na_ordem_e_sem_linha_em_branco_dupla(
        self, monkeypatch, saida, tmp_path
    ):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(capas=["[1] image/jpeg"]),
            {"disponivel": False, "motivo": "x"},
        )

        titulos = saida.titulos()
        assert titulos[0] == "🔍 INSPETOR DE ÁUDIO"
        assert [t.split(" (")[0] for t in titulos[1:]] == [
            "🏷️  TAGS",
            "🖼️  CAPAS EMBUTIDAS",
            "🔬 AUTENTICIDADE",
        ]
        # Só o primeiro painel abre com linha em branco; os outros encostam
        # no fim do anterior (cada caixa já termina com uma).
        assert saida.blocos[0].startswith("\n")
        assert not any(b.startswith("\n") for b in saida.blocos[1:])
