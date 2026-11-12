"""`ui.estado`: o estado do terminal vive num objeto só, restaurável."""

import dataclasses
import pathlib
import re

import pytest

from qobuz_dl import ui

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CAMPOS = {f.name for f in dataclasses.fields(ui.EstadoUI)}


@pytest.fixture(autouse=True)
def _restaura():
    antes = dataclasses.asdict(ui.estado)
    yield
    for nome, valor in antes.items():
        setattr(ui.estado, nome, valor)


def test_campos_e_padroes():
    assert CAMPOS == {
        "quiet",
        "verbose",
        "color_enabled",
        "unicode_enabled",
        "ui_mode",
        "layout_interativo",
        "pending_status",
        "ultima_linha_vazia",
    }
    novo = ui.EstadoUI()
    assert (novo.quiet, novo.verbose) == (False, False)
    assert novo.color_enabled is None and novo.unicode_enabled is None
    assert novo.ui_mode is None
    assert novo.layout_interativo == "auto"


def test_redefinir_estado_volta_ao_padrao():
    ui.configure(quiet=True, verbose=True, color=True, unicode=True, ui_mode="compact")
    ui.redefinir_estado()
    assert dataclasses.asdict(ui.estado) == dataclasses.asdict(ui.EstadoUI())


def test_redefinir_mantem_a_mesma_instancia():
    objeto = ui.estado
    ui.redefinir_estado()
    assert ui.estado is objeto


def test_estado_temporario_muda_e_restaura():
    ui.configure(quiet=False)
    with ui.estado_temporario(quiet=True, color_enabled=False) as e:
        assert e is ui.estado
        assert ui.estado.quiet is True
        assert ui.color_enabled() is False
    assert ui.estado.quiet is False


def test_estado_temporario_restaura_mesmo_com_erro():
    with pytest.raises(RuntimeError):
        with ui.estado_temporario(verbose=True):
            raise RuntimeError("falha")
    assert ui.estado.verbose is False


def test_estado_temporario_recusa_campo_inexistente():
    with pytest.raises(AttributeError, match="quiett"):
        with ui.estado_temporario(quiett=True):
            pass


def test_configure_escreve_no_objeto():
    ui.configure(quiet=True, ui_mode="comfortable")
    assert ui.estado.quiet is True and ui.estado.ui_mode == "comfortable"
    ui.configure(ui_mode="auto")
    assert ui.estado.ui_mode is None


def test_configure_recusa_modo_invalido_sem_mexer_no_estado():
    ui.configure(quiet=False)
    with pytest.raises(ValueError):
        ui.configure(quiet=True, ui_mode="gigante")
    # quiet já tinha sido aplicado antes do erro (a ordem de configure é fixa)
    assert ui.estado.ui_mode is None


def test_quiet_silencia_emit_e_emit_always_ignora(capsys):
    with ui.estado_temporario(quiet=True):
        ui.emit("escondido")
        ui.emit_always("visivel")
    saida = capsys.readouterr().out
    assert "escondido" not in saida and "visivel" in saida


def test_variaveis_globais_antigas_sumiram():
    for antigo in (
        "_quiet",
        "_verbose",
        "_color_enabled",
        "_unicode_enabled",
        "_ui_mode",
        "_pending_status",
        "_ultima_linha_vazia",
    ):
        assert not hasattr(ui, antigo), antigo


def test_nenhum_codigo_ou_teste_usa_os_nomes_antigos():
    """Patch em `ui._quiet` & cia. viraria no-op silencioso: proibido."""
    padrao = re.compile(
        r"\bui\._(?:quiet|verbose|color_enabled|unicode_enabled|ui_mode"
        r"|pending_status|ultima_linha_vazia)\b"
        r"|\(ui,\s*[\"']_(?:quiet|verbose|color_enabled|unicode_enabled"
        r"|ui_mode|pending_status|ultima_linha_vazia)[\"']"
    )
    achados = []
    for pasta in ("qobuz_dl", "tests", "tools"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            if arq.name == "test_estado_ui.py":
                continue
            for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
                if padrao.search(linha):
                    achados.append(f"{arq.relative_to(RAIZ)}:{n}: {linha.strip()}")
    assert not achados, "\n".join(achados)


def test_ui_nao_usa_global_para_o_estado():
    fonte = (RAIZ / "qobuz_dl" / "ui.py").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*global\s+_", fonte)
