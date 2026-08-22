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

    # Dependências fixadas diretamente como uma lista Python
    install_requires=[
        "pathvalidate",
        "httpx",
        "brotli",
        "aiosqlite",
        "rapidfuzz",
        "send2trash",
        "humanize",
        "pyacoustid",
        "watchdog",
        "aiofiles",
        "tenacity",
        "platformdirs",
        "charset_normalizer",
        "mutagen",
        "tqdm",
        "beautifulsoup4",
        "colorama",
        # ver qopy.py: pycryptodome quebra no a-Shell (framework nativo ausente)
        "cryptography",
        "keyring",
        "questionary",
        "prompt_toolkit",
        "Pillow>=10.0.0",  # usado so' pra recompactar a capa embutida quando excede o limite de 16MB do FLAC
    ],
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
