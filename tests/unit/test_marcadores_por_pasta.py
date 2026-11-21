"""O marcador `unit` vem da PASTA (hook em tests/conftest.py), sem pytestmark."""


def test_pasta_unit_recebe_o_marcador_unit(request):
    assert request.node.get_closest_marker("unit") is not None
