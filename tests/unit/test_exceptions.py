"""Testes de cobertura total para qobuz_dl/exceptions.py."""

import pytest

from qobuz_dl.exceptions import (
    AuthenticationError,
    DownloadError,
    InvalidAppCredentialsError,
    InvalidAppSecretError,
    InvalidQuality,
    NoActiveSubscriptionError,
    NonStreamable,
    QobuzDLException,
    ResourceNotFoundError,
)


# ---------------------------------------------------------------------------
# Hierarquia
# ---------------------------------------------------------------------------

def test_qobuz_dl_exception_herda_de_exception():
    assert issubclass(QobuzDLException, Exception)


@pytest.mark.parametrize(
    "exc_cls",
    [
        AuthenticationError,
        ResourceNotFoundError,
        DownloadError,
        InvalidAppCredentialsError,
        NoActiveSubscriptionError,
        InvalidAppSecretError,
        InvalidQuality,
        NonStreamable,
    ],
)
def test_todas_herdam_de_qobuz_dl_exception(exc_cls):
    assert issubclass(exc_cls, QobuzDLException)


@pytest.mark.parametrize(
    "exc_cls",
    [
        AuthenticationError,
        ResourceNotFoundError,
        DownloadError,
        InvalidAppCredentialsError,
        NoActiveSubscriptionError,
        InvalidAppSecretError,
        InvalidQuality,
        NonStreamable,
    ],
)
def test_todas_herdam_de_exception(exc_cls):
    assert issubclass(exc_cls, Exception)


# ---------------------------------------------------------------------------
# Instanciação sem mensagem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc_cls",
    [
        QobuzDLException,
        AuthenticationError,
        ResourceNotFoundError,
        DownloadError,
        InvalidAppCredentialsError,
        NoActiveSubscriptionError,
        InvalidAppSecretError,
        InvalidQuality,
        NonStreamable,
    ],
)
def test_instancia_sem_mensagem(exc_cls):
    exc = exc_cls()
    assert isinstance(exc, exc_cls)


# ---------------------------------------------------------------------------
# Instanciação com mensagem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc_cls, msg",
    [
        (QobuzDLException, "erro base"),
        (AuthenticationError, "credenciais inválidas"),
        (ResourceNotFoundError, "álbum não encontrado"),
        (DownloadError, "falha no stream"),
        (InvalidAppCredentialsError, "app_id inválido"),
        (NoActiveSubscriptionError, "sem assinatura ativa"),
        (InvalidAppSecretError, "secret inválido"),
        (InvalidQuality, "qualidade 999 inválida"),
        (NonStreamable, "faixa não disponível para stream"),
    ],
)
def test_instancia_com_mensagem(exc_cls, msg):
    exc = exc_cls(msg)
    assert str(exc) == msg


# ---------------------------------------------------------------------------
# raise / catch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc_cls",
    [
        QobuzDLException,
        AuthenticationError,
        ResourceNotFoundError,
        DownloadError,
        InvalidAppCredentialsError,
        NoActiveSubscriptionError,
        InvalidAppSecretError,
        InvalidQuality,
        NonStreamable,
    ],
)
def test_pode_ser_lancada_e_capturada_pela_base(exc_cls):
    with pytest.raises(QobuzDLException):
        raise exc_cls("teste")


def test_authentication_error_capturada_diretamente():
    with pytest.raises(AuthenticationError):
        raise AuthenticationError("auth falhou")


def test_download_error_capturada_diretamente():
    with pytest.raises(DownloadError):
        raise DownloadError("download falhou")


def test_non_streamable_capturada_diretamente():
    with pytest.raises(NonStreamable):
        raise NonStreamable()


def test_invalid_quality_capturada_diretamente():
    with pytest.raises(InvalidQuality):
        raise InvalidQuality("5 não é qualidade válida")


# ---------------------------------------------------------------------------
# Encadeamento (chaining)
# ---------------------------------------------------------------------------

def test_excecao_encadeada():
    causa = ValueError("valor ruim")
    with pytest.raises(DownloadError) as exc_info:
        try:
            raise causa
        except ValueError as err:
            raise DownloadError("download falhou") from err
    assert exc_info.value.__cause__ is causa
