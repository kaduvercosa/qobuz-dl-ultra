import os
from setuptools import setup, find_packages

# 1. NEW PACKAGE NAME (Must be unique on PyPI)
pkg_name = "qobuz-dl-ultra"

def get_version():
    init_path = os.path.join(os.path.dirname(__file__), "qobuz_dl", "__init__.py")
    try:
        with open(init_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split('"')[1]
    except Exception:
        pass
    return "2.3.7"

def read_file(fname):
    # Added encoding="utf-8" to prevent build errors with emojis in README
    with open(fname, "r", encoding="utf-8") as f:
        return f.read()

requirements = [
    "pathvalidate",
    "requests",
    "charset_normalizer",
    "mutagen",
    "tqdm",
    "beautifulsoup4",
    "colorama",
    # NOTE: cryptography was used in the original downloader, keeping it for safety
    "cryptography",
    "keyring",
    "aiohttp",
    "questionary",
    # core.py importa isso incondicionalmente no topo do arquivo (fora de
    # qualquer funcao) pra montar a TUI interativa. Sem essa linha aqui, um
    # `pip install` limpo (a-Shell, Colab, venv novo) instala o pacote sem
    # erro nenhum, mas o programa morre com sys.exit() no PRIMEIRO comando
    # rodado, ate' num `--help`.
    "prompt_toolkit",
    # lyrics_engine.py importa isso com try/except (nao derruba o programa
    # se faltar), mas sem declarar aqui o fallback de letras via Genius
    # fica mudo silenciosamente pra quem instalar via pip puro.
    "lyricsgenius",
]

setup(
    name=pkg_name,
    # 2. VERSION READ AUTOMATICALLY FROM __init__.py
    version=get_version(),  
    # 3. AUTHOR INFO
    author="Eduardo Verçosa (kaduvercosa)",
    author_email="kaduvercosa@users.noreply.github.com",
    description="The Ultimate Lossless and Hi-Res music downloader for Qobuz with ReplayGain and Classical metadata",
    long_description=read_file("README.md"),
    long_description_content_type="text/markdown",
    # 4. LINK TO YOUR FORK
    url="https://github.com/kaduvercosa/qobuz-dl-ultra", 
    
    project_urls={
        "Documentation": "https://github.com/kaduvercosa/qobuz-dl-ultra/wiki",
        "Source Code": "https://github.com/kaduvercosa/qobuz-dl-ultra",
        "Bug Tracker": "https://github.com/kaduvercosa/qobuz-dl-ultra/issues",
    },
    
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            # Keeping the original command names for backward compatibility
            "qobuz-dl = qobuz_dl:main",
            "qdl = qobuz_dl:main",
        ],
    },
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License (GPL)",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)