"""Testa qobuz_dl/__main__.py.

Só tem 1 linha medível (`from qobuz_dl.cli import main`) -- o guard
`if __name__ == "__main__":` está excluído da cobertura via
`exclude_also` em pyproject.toml (nunca roda sob pytest, cobrir isso é
medir o interpretador, não o programa). Nenhum teste em toda a suíte
importava `qobuz_dl.__main__` diretamente (só `qobuz_dl.cli`), então
essa única linha nunca executava.
"""


def test_importa_o_modulo_entrypoint_sem_executar_a_cli():
    import qobuz_dl.__main__ as entrypoint

    assert entrypoint.main is not None
