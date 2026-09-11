"""Testa qobuz_dl/settings.py: o objeto central de configuração que junta
argumentos de linha de comando com o config.ini. É consumido por quase
todo o resto do projeto (downloader, tagging), então um valor resolvido
errado aqui se propaga silenciosamente pra tudo que lê `self.settings.X`.

Três frentes:
  1. QobuzDLSettings() com kwargs vazios -- os defaults documentados.
  2. A lógica de since_date/before_date (ano isolado vira o primeiro/
     último dia do ano) e de segment_workers (heurística de CPU).
  3. from_arguments_configparser() -- a fusão CLI + config.ini, com foco
     nos casos de precedência mais fáceis de acertar errado: flags
     "opt-out" (no_lrc_files, no_embed_lyrics, no_multi_tags) que têm
     prioridade sobre tudo mais pra DESLIGAR uma opção.
"""

import configparser
import types

import pytest

from qobuz_dl.constants import (
    DEFAULT_FOLDER,
    DEFAULT_MULTIPLE_DISC_TRACK,
    DEFAULT_TRACK,
)
from qobuz_dl.settings import QobuzDLSettings, _merge_bool_opt_in, _merge_bool_opt_out

pytestmark = pytest.mark.unit


def _args(**kwargs):
    """Simula um argparse.Namespace -- só os atributos passados existem,
    o resto levanta AttributeError se acessado sem getattr(..., default)."""
    return types.SimpleNamespace(**kwargs)


def _config(secoes=None):
    """ConfigParser vazio, ou pré-populado com a seção [qobuz]."""
    cp = configparser.ConfigParser()
    if secoes:
        cp["qobuz"] = secoes
    return cp


# ---------------------------------------------------------------------------
# Defaults de QobuzDLSettings() puro
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_sem_kwargs_usa_os_defaults_documentados(self):
        s = QobuzDLSettings()
        assert s.default_folder == "QobuzDownloads"
        assert s.default_quality == 6
        assert s.default_limit == 20
        assert s.no_m3u is False
        assert s.embed_art is False
        assert s.lrc_files is True
        assert s.embed_lyrics is True
        assert s.multiple_disc_prefix == "CD"
        assert s.fallback_folder_format == DEFAULT_FOLDER
        assert s.multiple_disc_track_format == DEFAULT_MULTIPLE_DISC_TRACK

    def test_kwargs_explicitos_sobrescrevem_o_default(self):
        s = QobuzDLSettings(default_quality=27, no_m3u=True)
        assert s.default_quality == 27
        assert s.no_m3u is True

    def test_max_workers_e_convertido_pra_inteiro(self):
        s = QobuzDLSettings(max_workers="4")
        assert s.max_workers == 4
        assert isinstance(s.max_workers, int)

    def test_workers_sao_limitados_a_valores_seguros(self):
        assert QobuzDLSettings(max_workers=999).max_workers == 16
        assert QobuzDLSettings(max_workers=-2).max_workers == 1
        assert QobuzDLSettings(segment_workers=999).segment_workers == 16


class TestSinceBeforeDate:
    def test_ano_isolado_no_since_vira_primeiro_dia_do_ano(self):
        assert QobuzDLSettings(since_date="2020").since_date == "2020-01-01"

    def test_ano_isolado_no_before_vira_ultimo_dia_do_ano(self):
        assert QobuzDLSettings(before_date="2020").before_date == "2020-12-31"

    def test_data_completa_e_preservada_sem_mudanca(self):
        s = QobuzDLSettings(since_date="2020-06-15", before_date="2021-03-10")
        assert s.since_date == "2020-06-15"
        assert s.before_date == "2021-03-10"

    def test_sem_data_vira_none(self):
        s = QobuzDLSettings()
        assert s.since_date is None
        assert s.before_date is None

    def test_string_vazia_vira_none(self):
        s = QobuzDLSettings(since_date="", before_date="")
        assert s.since_date is None
        assert s.before_date is None


class TestSegmentWorkers:
    def test_valor_explicito_positivo_e_respeitado(self):
        assert QobuzDLSettings(segment_workers=5).segment_workers == 5

    def test_zero_ou_ausente_cai_na_heuristica_de_cpu(self, monkeypatch):
        import qobuz_dl.settings as settings_mod

        monkeypatch.setattr(settings_mod.os, "cpu_count", lambda: 4)
        # 4 CPUs * 2 = 8, dentro do limite [2, 8] -> fica 8
        assert QobuzDLSettings(segment_workers=0).segment_workers == 8

    def test_heuristica_tem_piso_de_2(self, monkeypatch):
        import qobuz_dl.settings as settings_mod

        monkeypatch.setattr(settings_mod.os, "cpu_count", lambda: 1)
        # 1 CPU * 2 = 2, ja' no piso
        assert QobuzDLSettings().segment_workers == 2

    def test_heuristica_tem_teto_de_8(self, monkeypatch):
        import qobuz_dl.settings as settings_mod

        monkeypatch.setattr(settings_mod.os, "cpu_count", lambda: 64)
        assert QobuzDLSettings().segment_workers == 8

    def test_cpu_count_none_nao_quebra(self, monkeypatch):
        """os.cpu_count() pode devolver None em ambientes restritos --
        tem que cair no "or 4" do código, não explodir multiplicando
        None por 2."""
        import qobuz_dl.settings as settings_mod

        monkeypatch.setattr(settings_mod.os, "cpu_count", lambda: None)
        assert QobuzDLSettings().segment_workers == 8  # 4 (fallback) * 2


# ---------------------------------------------------------------------------
# Helpers de merge CLI x config.ini
# ---------------------------------------------------------------------------
class TestMergeBoolOptOut:
    """CLI, quando fornecida, sempre vence -- mesmo pra desligar algo que
    o config.ini tinha ligado."""

    def test_cli_ausente_usa_config(self):
        assert _merge_bool_opt_out(_args(), "no_m3u", True) is True
        assert _merge_bool_opt_out(_args(), "no_m3u", False) is False

    def test_cli_true_vence_config_false(self):
        assert _merge_bool_opt_out(_args(no_m3u=True), "no_m3u", False) is True

    def test_cli_false_vence_config_true(self):
        assert _merge_bool_opt_out(_args(no_m3u=False), "no_m3u", True) is False


class TestMergeBoolOptIn:
    """no_<opção> sempre desliga, mesmo que <opção> também esteja True."""

    def test_no_opcao_desliga_mesmo_com_opcao_ligada(self):
        assert (
            _merge_bool_opt_in(_args(no_cover=True, cover=True), "cover", True) is False
        )

    def test_opcao_liga_sem_o_no_(self):
        assert _merge_bool_opt_in(_args(cover=True), "cover", False) is True

    def test_sem_nenhum_dos_dois_usa_config(self):
        assert _merge_bool_opt_in(_args(), "cover", True) is True
        assert _merge_bool_opt_in(_args(), "cover", False) is False


# ---------------------------------------------------------------------------
# from_arguments_configparser()
# ---------------------------------------------------------------------------
class TestFromArgumentsConfigparser:
    def test_cli_tem_precedencia_sobre_config(self):
        args = _args(directory="/cli/path", quality=27)
        config = _config({"default_folder": "/config/path", "default_quality": "5"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.default_folder == "/cli/path"
        assert str(s.default_quality) == "27"

    def test_config_preenche_quando_cli_nao_fornece(self):
        args = _args()
        config = _config({"default_folder": "/config/path"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.default_folder == "/config/path"

    def test_sem_cli_e_sem_config_usa_o_default_da_classe(self):
        args = _args()
        config = _config()

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.default_folder == "QobuzDownloads"
        assert s.track_format == DEFAULT_TRACK

    def test_secrets_do_config_sao_divididos_por_virgula_sem_vazios(self):
        args = _args()
        config = _config({"secrets": "abc,def,,ghi"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.secrets == ["abc", "def", "ghi"]

    def test_no_lrc_files_desliga_mesmo_sem_flag_positiva_no_cli(self):
        args = _args()
        config = _config({"no_lrc_files": "true"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.lrc_files is False

    def test_lrc_files_liga_por_padrao_sem_no_lrc_files(self):
        args = _args()
        config = _config()

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.lrc_files is True

    def test_no_embed_lyrics_no_cli_forca_false_mesmo_com_config_true(self):
        args = _args(no_embed_lyrics=True)
        config = _config({"embed_lyrics": "true"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.embed_lyrics is False

    def test_no_multi_tags_no_cli_forca_false_mesmo_com_config_true(self):
        args = _args(no_multi_tags=True)
        config = _config({"multi_value_tags": "true"})

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.multi_value_tags is False

    def test_multi_value_tags_do_cli_e_usado_sem_o_no_(self):
        args = _args(multi_value_tags=True)
        config = _config()

        s = QobuzDLSettings.from_arguments_configparser(args, config)

        assert s.multi_value_tags is True

    def test_secao_default_e_usada_se_qobuz_nao_existir(self):
        """ConfigParser sem [qobuz] -- from_arguments_configparser() cai
        pra seção DEFAULT em vez de quebrar com NoSectionError."""
        config = configparser.ConfigParser()
        config["DEFAULT"] = {"default_folder": "/via/default"}

        s = QobuzDLSettings.from_arguments_configparser(_args(), config)

        assert s.default_folder == "/via/default"

    def test_embed_art_default_e_true_quando_nada_e_informado(self):
        """embed_art usa fallback=True no config -- diferente da maioria
        dos outros booleans, que default pra False."""
        s = QobuzDLSettings.from_arguments_configparser(_args(), _config())
        assert s.embed_art is True
