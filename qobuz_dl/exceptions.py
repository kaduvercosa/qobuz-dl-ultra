"""Exceções customizadas para o qobuz-dl."""


class QobuzDLException(Exception):
    """Exceção base para o qobuz-dl."""


class AuthenticationError(QobuzDLException):
    """Lançada quando a autenticação de login ou token falha."""


class ResourceNotFoundError(QobuzDLException):
    """Lançada quando um álbum, faixa ou playlist não é encontrada."""


class DownloadError(QobuzDLException):
    """Lançada quando ocorre uma falha no download ou stream."""


class InvalidAppCredentialsError(QobuzDLException):
    """Lançada quando app_id ou app_secret são inválidos."""


class NoActiveSubscriptionError(QobuzDLException):
    """Lançada quando a conta não possui assinatura ativa de streaming/download."""


class InvalidAppSecretError(QobuzDLException):
    pass


class InvalidQuality(QobuzDLException):
    pass


class NonStreamable(QobuzDLException):
    pass
