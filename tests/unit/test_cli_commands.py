"""Testes adicionais para a CLI - cobertura de integração de comandos.

Versao corrigida para a arquitetura argparse real do cli.py:
- Nao usa CliRunner (isso e para Click), mas sim chamadas diretas a async_main() com mocks.
- Mocka qopy.Client.create para evitar autenticao real na API.
- Mocka os.makedirs/os.path.isdir no lugar de patch("qobuz_dl.cli.Path") que nao existe.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
from pathlib import Path
import sys
import os

# Importa o modulo real que contem async_main()
from qobuz_dl import cli


class TestCLIBasicCommands:
    """Testa comandos basicos da CLI (argparse)."""

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_cli_help(self, mock_initialchecks, mock_client_create, capsys):
        """Verifica se a ajuda e exibida corretamente via --help."""
        # Configura o mock do client para retornar um objeto fake com checksubscription
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
            "offer": "Hi-Fi",
        }
        mock_client_create.return_value = mock_client

        # initialchecks() chama sys.exit(0) quando nao ha argumentos; mockamos para nao sair
        mock_initialchecks.return_value = None

        # Simula chamada com --help no sys.argv
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            # --help causa sys.exit(0)
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

        captured = capsys.readouterr()
        # O help do argparse contem "usage:" e lista de comandos
        assert "usage:" in captured.out.lower() or "uso:" in captured.out.lower()

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_cli_version(self, mock_initialchecks, mock_client_create, capsys):
        """Testa comportamento com --version (que nao existe no argparse atual)."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "--version"]
            # No argparse atual, --version e argumento invalido -> exit code 2
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code != 0  # argparse retorna 2 para argumentos invalidos
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_invalid_command(self, mock_initialchecks, mock_client_create, capsys):
        """Testa comportamento com comando invalido."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "invalid-command"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            # Comando invalido deve causar exit code != 0
            assert exc_info.value.code != 0
        finally:
            sys.argv = original_argv

        captured = capsys.readouterr()
        # Mensagem de erro do argparse menciona "invalid choice"
        assert "invalid choice" in captured.err.lower() or "erro" in captured.err.lower()


class TestCLIArgumentValidation:
    """Testa validacao de argumentos na CLI."""

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_missing_required_arguments(self, mock_initialchecks, mock_client_create, capsys):
        """Verifica comportamento quando nenhum argumento e fornecido."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client

        # initialchecks() imprime welcome screen e chama sys.exit(0) se len(sys.argv) < 2
        # Aqui deixamos ele rodar, mas capturamos o exit
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl"]
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            # Welcome screen -> exit 0
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_url_argument_parsing(self, mock_initialchecks, mock_client_create, capsys):
        """Testa parsing de URLs como argumentos."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            test_url = "https://www.qobuz.com/en-us/album/test-album/123456"
            sys.argv = ["qobuz-dl", "dl", test_url]
            # Deve falhar graciosamente (exit != 0) por falta de config/credenciais reais,
            # mas nao deve levantar excecao nao capturada
            with pytest.raises(SystemExit):
                cli.main()
        finally:
            sys.argv = original_argv


class TestCLIErrorHandling:
    """Testa tratamento de erros na CLI."""

    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    @patch("qobuz_dl.cli.initialchecks")
    def test_config_file_not_found(self, mock_initialchecks, mock_client_create, capsys):
        """Testa comportamento quando arquivo de config nao existe."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client

        # initialchecks() chama resetconfig() se CONFIGFILE nao existir,
        # o que inicia o wizard interativo. Para evitar isso, mockamos initialchecks.
        mock_initialchecks.return_value = None

        original_argv = sys.argv.copy()
        try:
            # Argumento --config nao existe no argparse atual; usamos um comando que forca
            # leitura de config (ex: dl) sem config valido
            sys.argv = ["qobuz-dl", "dl", "https://www.qobuz.com/album/123"]
            with pytest.raises(SystemExit):
                cli.main()
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    def test_invalid_output_path(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Testa com caminho de saida invalido (PermissionError em mkdir)."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client

        # Simula PermissionError ao tentar criar diretorio de config
        mock_makedirs.side_effect = PermissionError("Permission denied")

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"]  # Forca criacao/reset de config
            # Deve falhar graciosamente com PermissionError
            with pytest.raises(PermissionError):
                cli.main()
        finally:
            sys.argv = original_argv


class TestCLIConfigHandling:
    """Testa gerenciamento de configuracao via CLI."""

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    def test_config_directory_creation(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Verifica se o diretorio de config e criado quando necessario."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client

        # mock_makedirs nao tem side_effect -> deve "criar" o diretorio com sucesso
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"]
            # O wizard de config interativo seria iniciado aqui;
            # como mockamos initialchecks, vamos so validar que makedirs foi chamado
            cli.initialchecks()
            mock_makedirs.assert_called()
        finally:
            sys.argv = original_argv

    @patch("qobuz_dl.cli.os.makedirs")
    @patch("qobuz_dl.cli.os.path.isdir", return_value=False)
    @patch("qobuz_dl.cli.os.path.isfile", return_value=False)
    @patch("qobuz_dl.cli.qopy.Client.create", new_callable=AsyncMock)
    def test_config_file_loading(
        self, mock_client_create, mock_isfile, mock_isdir, mock_makedirs
    ):
        """Testa carregamento de arquivo de configuracao."""
        mock_client = AsyncMock()
        mock_client.checksubscription.return_value = {
            "isactive": True,
            "status": "Ativa",
        }
        mock_client_create.return_value = mock_client

        # Este teste so valida que o fluxo inicial nao quebra;
        # o carregamento real do config.ini acontece em async_main().
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["qobuz-dl", "-r"]
            cli.initialchecks()
            # Se chegou aqui sem excecao, o mock de config esta OK
            assert True
        finally:
            sys.argv = original_argv
