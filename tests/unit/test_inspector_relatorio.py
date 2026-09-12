"""Testa `_mostrar_relatorio()` de qobuz_dl/inspector.py -- a função que
decide QUAIS chamadas de `ui.*` (banner/section/kv/warn/ok/error/detail)
acontecem, dado o relatório de tags e o resultado da checagem de
genuinidade. `_gerar_grafico_html` é substituída por um stub em todo
teste aqui -- ela já tem sua própria suíte em test_inspector_grafico.py,
não faz sentido duplicar; aqui o que importa é o DESPACHO de UI ao redor
dela (sucesso / sem dados / exceção).

Verificação por "spy": cada função de qobuz_dl.ui vira uma função que só
registra `(nome, args)` numa lista -- sem tocar terminal de verdade.
"""

import pytest

from qobuz_dl import inspector

pytestmark = pytest.mark.unit


@pytest.fixture
def ui_spy(monkeypatch):
    chamadas = []

    def _fabrica_spy(nome):
        def _fn(*args, **kwargs):
            chamadas.append((nome, args))

        return _fn

    for nome in (
        "banner",
        "section",
        "kv",
        "blank",
        "detail",
        "warn",
        "ok",
        "error",
    ):
        monkeypatch.setattr(inspector.ui, nome, _fabrica_spy(nome))

    return chamadas


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
        self, monkeypatch, ui_spy, tmp_path
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

        assert ("warn", ("sem numpy instalado",)) in ui_spy
        assert chamado_grafico == []

    def test_cor_ok_chama_ui_ok_e_avisa_grafico_salvo(
        self, monkeypatch, ui_spy, tmp_path
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

        assert ("ok", ("Parece genuíno",)) in ui_spy
        assert any(nome == "ok" and destino_fake in args[0] for nome, args in ui_spy)

    def test_cor_warn_chama_ui_warn(self, monkeypatch, ui_spy, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            _genuinidade_disponivel(cor="warn", mensagem="Inconclusivo"),
        )

        assert ("warn", ("Inconclusivo",)) in ui_spy

    def test_cor_error_chama_ui_error(self, monkeypatch, ui_spy, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(),
            _genuinidade_disponivel(cor="error", mensagem="Provavelmente upsample"),
        )

        assert ("error", ("Provavelmente upsample",)) in ui_spy

    def test_grafico_none_avisa_dados_insuficientes(
        self, monkeypatch, ui_spy, tmp_path
    ):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"), _dados(), _genuinidade_disponivel()
        )

        assert any(
            nome == "warn" and "dados de espectro suficientes" in args[0]
            for nome, args in ui_spy
        )

    def test_grafico_lanca_excecao_vira_ui_error_sem_propagar(
        self, monkeypatch, ui_spy, tmp_path
    ):
        def _explode(caminho, gen):
            raise RuntimeError("disco cheio")

        monkeypatch.setattr(inspector, "_gerar_grafico_html", _explode)

        # Não pode propagar -- _mostrar_relatorio despacha na tela e segue,
        # não derruba o resto de run_inspector().
        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"), _dados(), _genuinidade_disponivel()
        )

        assert any(
            nome == "error" and "disco cheio" in args[0] for nome, args in ui_spy
        )

    def test_sem_tags_mostra_mensagem_de_nenhuma_tag(
        self, monkeypatch, ui_spy, tmp_path
    ):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(tags={}),
            {"disponivel": False, "motivo": "x"},
        )

        assert any(
            nome == "detail" and "nenhuma tag encontrada" in args[0]
            for nome, args in ui_spy
        )

    def test_com_capas_mostra_secao_de_capas(self, monkeypatch, ui_spy, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(capas=["[1] image/jpeg, 500x500, 200 KB"]),
            {"disponivel": False, "motivo": "x"},
        )

        assert any(
            nome == "detail" and "image/jpeg" in args[0] for nome, args in ui_spy
        )

    def test_sem_capas_nao_mostra_secao_de_capas(self, monkeypatch, ui_spy, tmp_path):
        monkeypatch.setattr(inspector, "_gerar_grafico_html", lambda c, g: None)

        inspector._mostrar_relatorio(
            str(tmp_path / "faixa.flac"),
            _dados(capas=[]),
            {"disponivel": False, "motivo": "x"},
        )

        assert not any("CAPAS" in str(args) for _, args in ui_spy)
