"""Shim de compatibilidade -- a configuracao real vive no pyproject.toml.

CONTEXTO DA MUDANCA
-------------------
Antes, este arquivo duplicava TODA a configuracao do pacote: nome, versao,
autor, classifiers, `python_requires` e a lista completa de `install_requires`.
Com `pyproject.toml` no repositorio declarando as mesmas coisas, existiam
tres fontes de verdade concorrentes (pyproject.toml, setup.py e
requirements.txt) -- e elas JA' haviam divergido na pratica:

  * ``Pillow>10.0.0`` no pyproject vs ``Pillow>=10.0.0`` aqui e no
    requirements.txt (o primeiro EXCLUI a versao 10.0.0);
  * ``python_requires=">=3.6"`` em ambos, uma versao onde o projeto nem
    compila.

Como o build-backend declarado em ``[build-system]`` e' o
``setuptools.build_meta``, o setuptools le' o ``[project]`` do pyproject.toml
e este ``setup.py`` nao precisa repetir nada. Ele fica apenas para nao quebrar
fluxos legados que ainda invocam ``python setup.py ...`` diretamente.

Para alterar dependencias, versao minima do Python ou metadados, edite
SOMENTE o ``pyproject.toml``.
"""

from setuptools import setup

setup()
