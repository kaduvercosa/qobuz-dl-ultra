"""Testes dos callbacks de teclado internos de _tui_select()."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class Size:
    columns = 100
    rows = 40


class Output:
    def get_size(self):
        return Size()


class Event:
    def __init__(self):
        self.app = SimpleNamespace(
            invalidate=lambda: None,
            exited=None,
            exit=self._exit,
        )

    def _exit(self, result=None, exception=None):
        self.app.exited = (result, exception)


class App:
    result = None
    instance = None

    def __init__(self, layout, key_bindings, **kwargs):
        self.output = Output()
        self.key_bindings = key_bindings
        self.layout = layout
        self.result = App.result
        App.instance = self

    async def run_async(self):
        return self.result


def _key_names(binding):
    names = set()
    for item in getattr(binding, "keys", ()):
        value = getattr(item, "value", item)
        names.add(str(value))
        names.add(str(value).lower())
        names.add(str(getattr(item, "name", "")).lower())
    return names


def _handler(key):
    wanted = str(key).lower()
    aliases = {
        "enter": {"enter", "return", "c-m", "\r", "\n"},
        "escape": {"escape", "esc"},
        "c-c": {"c-c", "ctrl-c"},
    }
    candidates = aliases.get(wanted, {wanted})
    for binding in App.instance.key_bindings.bindings:
        if _key_names(binding) & candidates:
            return binding.handler
    raise AssertionError(f"binding não encontrada: {key}")


def _numeric_handler(key):
    for binding in App.instance.key_bindings.bindings:
        if str(key) in _key_names(binding):
            return binding.handler
    raise AssertionError(f"binding numérica não encontrada: {key}")


def _space_handler():
    for binding in App.instance.key_bindings.bindings:
        names = _key_names(binding)
        if "space" in names or " " in names:
            return binding.handler
    raise AssertionError("binding space não encontrada")


def _enter_handler():
    handler = _handler("enter")
    if handler:
        return handler
    raise AssertionError("binding enter não encontrada")


async def test_callbacks_navegacao_e_selecao_multipla(monkeypatch):
    App.result = None
    monkeypatch.setattr(core, "Application", App)

    await core._tui_select(
        "Título", ["a", "b", "c"], is_multi=True, item_category="filter"
    )

    for key in ("down", "up", "j", "k", "pageup", "pagedown", "g", "G", "r"):
        result = _handler(key)(Event())
        if result is not None:
            await result

    for key in ("1", "2"):
        _numeric_handler(key)(Event())

    space = _space_handler()
    space(Event())
    space(Event())
    _handler("t")(Event())
    _handler("t")(Event())
    _enter_handler()(Event())


async def test_callbacks_cancelamento(monkeypatch):
    App.result = None
    monkeypatch.setattr(core, "Application", App)

    await core._tui_select("Título", ["a"], is_multi=False)

    for key in ("escape", "c-c"):
        event = Event()
        _handler(key)(event)
        assert event.app.exited[1] is not None


async def test_callback_enter_lista_vazia_nao_sai(monkeypatch):
    App.result = None
    monkeypatch.setattr(core, "Application", App)

    await core._tui_select("Título", [], is_multi=True)
    event = Event()
    _enter_handler()(event)
    assert event.app.exited is None


async def test_callbacks_space_e_t_com_lista_vazia_nao_quebram(monkeypatch):
    """Guardas `if not options_dicts: return` de space/t (linhas 193 e
    203) -- só o de enter (213) tinha teste com lista vazia."""
    App.result = None
    monkeypatch.setattr(core, "Application", App)

    await core._tui_select("Título", [], is_multi=True)

    _space_handler()(Event())  # não pode levantar
    _handler("t")(Event())  # não pode levantar


async def test_callback_enter_modo_single_confirma_item_sob_cursor(monkeypatch):
    """Modo não-multi (is_multi=False): enter confirma o item sob o
    cursor direto, sem a lógica de seleção múltipla (linha 222) -- os
    testes existentes de single-select só cobriam escape/ctrl-c."""
    App.result = None
    monkeypatch.setattr(core, "Application", App)

    await core._tui_select("Título", ["a", "b"], is_multi=False)

    event = Event()
    _enter_handler()(event)

    assert event.app.exited == (("a", 0), None)


async def test_run_async_devolvendo_excecao_e_relancada(monkeypatch):
    """app.run_async() pode devolver a própria exceção em vez de
    levantá-la (é assim que escape/ctrl-c saem do Application de
    verdade) -- _tui_select precisa relançar isso, não devolver a
    exceção como se fosse um resultado válido."""
    App.result = RuntimeError("cancelado pelo usuário")
    monkeypatch.setattr(core, "Application", App)

    with pytest.raises(RuntimeError, match="cancelado pelo usuário"):
        await core._tui_select("Título", ["a"], is_multi=False)
