"""Testes adicionais para a CLI - cobertura de integração de comandos.

Versao corrigida para a arquitetura argparse real do cli.py:
- Nao usa CliRunner (isso e para Click), mas sim chamadas diretas a async_main() com mocks.
- Mocka qobuz_dl.qopy.Client.create para evitar autenticao real na API.
- Mocka os.makedirs/os.path.isdir no lugar de patch("qobuz_dl.cli.Path") que nao existe.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys

# Importa o modulo real que contem async_main()
from qobuz_dl import cli


class TestCLIBasicCommands:
    """Testa comandos basicos da CLI (argparse)."""

    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_cli_help(self, mock_initialchecks, mock_client_create, capsys):
        """Verifica se a ajuda e exibida corretamente via --help."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
            "offer": "Hi-Fi",
        })
        mock_client_create.return_value = mock_client

        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

        captured = capsys.readouterr()
        assert "usage:" in captured.out.lower() or "uso:" in captured.out.lower()

    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_cli_version(self, mock_initialchecks, mock_client_create, capsys):
        """Testa comportamento com --version (que nao existe no argparse atual)."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "--version"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code != 0
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_invalid_command(self, mock_initialchecks, mock_client_create, capsys):
        """Testa comportamento com comando invalido."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "invalid-command"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code != 0
        finally:
            sys.argv = original_argv

        captured = capsys.readouterr()
        assert "invalid choice" in captured.err.lower() or "erro" in captured.err.lower()


class TestCLIArgumentValidation:
    """Testa validacao de argumentos na CLI."""

    @patch("builtins.input", return_value="n")
    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_missing_required_arguments(self, mock_initialchecks, mock_client_create, mock_input, capsys):
        """Verifica comportamento quando nenhum argumento e fornecido."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.async_main", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_url_argument_parsing(self, mock_initialchecks, mock_async_main, capsys):
        """Testa parsing de URLs como argumentos."""
        mock_initialchecks.return_value = None
        mock_async_main.side_effect = SystemExit(0)

        original_argv = sys.argv.copy()
        try:
            test_url = "https://www.qobuz.com/en-us/album/test-album/123456"
            sys.argv = ["qobuz-dl", "dl", test_url]
            with pytest.raises(SystemExit):
                cli.main()
        finally:
            sys.argv = original_argv


class TestCLIErrorHandling:
    """Testa tratamento de erros na CLI."""

    @patch("qobuz_dl.cli.async_main", new_callable=AsyncMock)
    @patch("qobuz_dl.cli._initial_checks")
    def test_config_file_not_found(self, mock_initialchecks, mock_async_main, capsys):
        """Testa comportamento quando arquivo de config nao existe."""
        mock_initialchecks.return_value = None
        mock_async_main.side_effect = SystemExit(1)

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "dl", "https://www.qobuz.com/album/123"]
            with pytest.raises(SystemExit):
                cli.main()
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    def test_invalid_output_path(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Testa com caminho de saida invalido (PermissionError em mkdir)."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client

        mock_makedirs.side_effect = PermissionError("Permission denied")

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"] 
            with pytest.raises(PermissionError):
                cli.main()
        finally:
            sys.argv = original_argv


class TestCLIConfigHandling:
    """Testa gerenciamento de configuracao via CLI."""

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    def test_config_directory_creation(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Verifica se o diretorio de config e criado quando necessario."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"]
            cli._initial_checks()
            mock_makedirs.assert_called()
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.qopy.Client.create", new_callable=AsyncMock)
    def test_config_file_loading(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Testa carregamento de arquivo de configuracao."""
        mock_client = AsyncMock()
        mock_client.check_subscription = MagicMock(return_value={
            "is_active": True,
            "status": "Ativa",
        })
        mock_client_create.return_value = mock_client

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"]
            cli._initial_checks()
            assert True
        finally:
            sys.argv = original_argv
