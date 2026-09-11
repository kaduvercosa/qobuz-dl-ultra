# Usa uma versão oficial e leve do Python. Alinhada com a versão usada
# no CI (tests.yml) -- o projeto suporta 3.10 a 3.14 (ver
# requires-python/classifiers no pyproject.toml), 3.12 é só o que
# testamos por padrão.
FROM python:3.12-slim

# Instala o FFmpeg (necessário pra conversão/verificação de áudio)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Usuário sem privilégios para execução. O HOME gravável também mantém
# config.ini, banco e downloads fora da instalação do pacote.
RUN useradd --create-home --shell /usr/sbin/nologin qobuz

# Copia somente os arquivos necessários para construir o pacote.
WORKDIR /tmp/source
COPY pyproject.toml README.md MANIFEST.in setup.py ./
COPY qobuz_dl ./qobuz_dl

# Instala o projeto direto do pyproject.toml -- ele é a ÚNICA fonte de
# verdade das dependências (ver tests/regression/test_dependencias.py).
# ANTES: `pip install -r requirements.txt || pip install .` tentava o
# requirements.txt primeiro e caía num fallback silencioso pro pyproject
# se desse errado -- se o requirements.txt estivesse desatualizado ou
# quebrado, a imagem buildava mesmo assim, escondendo o problema. Como o
# requirements.txt é só um espelho gerado a partir do pyproject
# (tools/gerar_requirements.py), instalar direto do pyproject elimina o
# fallback e a possibilidade de build "com sucesso" a partir de
# dependências erradas/desatualizadas.
RUN pip install --no-cache-dir .

USER qobuz
WORKDIR /home/qobuz

# Declara o comando base (o usuário só passa os argumentos, tipo 'dl' ou '--sync-db')
ENTRYPOINT ["python", "-m", "qobuz_dl"]
