import configparser
from types import SimpleNamespace

import pytest

from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


def _settings(**valores):
    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = valores
    return QobuzDLSettings.from_arguments_configparser(SimpleNamespace(), config)


def test_idioma_da_conta_tem_prioridade():
    s = _settings(account_language="pt", lyrics_translation_lang="en")
    assert s.lyrics_translation_lang == "pt"


def test_sem_idioma_da_conta_usa_preferencia_salva():
    s = _settings(lyrics_translation_lang="es")
    assert s.lyrics_translation_lang == "es"


def test_sem_nenhum_idioma_usa_pt():
    s = _settings()
    assert s.lyrics_translation_lang == "pt"


def test_idioma_da_conta_e_normalizado():
    s = _settings(account_language="  PT ")
    assert s.lyrics_translation_lang == "pt"
