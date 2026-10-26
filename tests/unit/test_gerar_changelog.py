"""Testa `tools/gerar_changelog.py` (changelog com IA do GitHub Models).

Nenhum teste toca a rede: `_chamar_modelo` é trocada por uma função falsa. O teste
com git de verdade só roda se o `git` existir (ex.: não existe no a-Shell).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.unit

_CAMINHO = os.path.join(
    os.path.dirname(__file__), "..", "..", "tools", "gerar_changelog.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("gerar_changelog", _CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


gc = _carregar()

COMMITS = [
    {"hash": "aaa1111", "titulo": "feat: nova flag --x", "corpo": ""},
    {"hash": "bbb2222", "titulo": "fix: corrige o bug y", "corpo": "detalhe do corpo"},
    {"hash": "ccc3333", "titulo": "Bump version to 1.0.1", "corpo": ""},
]

DIFF_EXEMPLO = """\
diff --git a/qobuz_dl/cli.py b/qobuz_dl/cli.py
index 111..222 100644
--- a/qobuz_dl/cli.py
+++ b/qobuz_dl/cli.py
@@ -1,3 +1,4 @@
--- um separador removido
+nova linha
diff --git a/tests/unit/test_cli.py b/tests/unit/test_cli.py
index 333..444 100644
--- a/tests/unit/test_cli.py
+++ b/tests/unit/test_cli.py
@@ -1,2 +1,3 @@
+def test_novo(): ...
"""


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Isola git, rede e pausas: cada teste monta o que precisa."""
    monkeypatch.setattr(gc, "PAUSA_ENTRE_LOTES", 0)
    monkeypatch.setattr(gc, "_coletar_commits", lambda de, para: list(COMMITS))
    monkeypatch.setattr(gc, "_url_base_repositorio", lambda: "https://github.com/o/r")
    monkeypatch.setattr(gc, "_resumo_estatisticas", lambda h: None)
    monkeypatch.setattr(gc, "_secao_arquivos", lambda de, para, url: [])
    monkeypatch.setattr(gc, "_numstat", lambda *a: [("3", "1", "qobuz_dl/cli.py")])
    monkeypatch.setattr(gc, "_diff_do_commit", lambda h: DIFF_EXEMPLO)


def _config(**extra):
    return gc.ConfigIA(token="t", **extra)


def _resposta(itens, resumo="Resumo."):
    return json.dumps({"resumo": resumo, "itens": itens})


# --- diff enxuto -------------------------------------------------------------


class TestEnxugarDiff:
    def test_tira_cabecalho_inutil_mas_preserva_linhas_do_hunk(self):
        saida = gc._enxugar_diff(DIFF_EXEMPLO, 5000)
        assert "index 111" not in saida
        assert "+++ b/qobuz_dl" not in saida
        # Uma linha removida que começa com "--" vira "--- ..." no diff: é conteúdo.
        assert "--- um separador removido" in saida

    def test_codigo_vem_antes_dos_testes(self):
        saida = gc._enxugar_diff(DIFF_EXEMPLO, 5000)
        assert saida.index("qobuz_dl/cli.py") < saida.index("tests/unit/test_cli.py")

    def test_respeita_limite_e_mostra_todos_os_arquivos(self):
        grande = "".join(
            f"diff --git a/qobuz_dl/m{i}.py b/qobuz_dl/m{i}.py\n@@ -1 +1 @@\n"
            + "+linha longa de código\n" * 200
            for i in range(10)
        )
        saida = gc._enxugar_diff(grande, 3000)
        assert len(saida) <= 3000
        for i in range(10):
            assert f"qobuz_dl/m{i}.py" in saida
        assert "linhas omitidas" in saida

    def test_diff_vazio(self):
        assert gc._enxugar_diff("", 1000) == ""


class TestRepartirLimite:
    def test_arquivo_grande_nao_engole_os_pequenos(self):
        cotas = gc._repartir_limite([100, 100, 5000], 1000)
        assert cotas[0] == 100 and cotas[1] == 100
        assert cotas[2] == 800

    def test_nunca_passa_do_tamanho_do_arquivo(self):
        assert gc._repartir_limite([50, 60], 10_000) == [50, 60]


# --- resposta da IA ----------------------------------------------------------


class TestRespostaDaIA:
    def test_extrai_json_dentro_de_cerca_de_codigo(self):
        assert gc._extrair_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_extrai_json_com_texto_em_volta(self):
        assert gc._extrair_json('Claro! {"a": 2} Pronto.') == {"a": 2}

    def test_json_invalido_levanta_erro_ia(self):
        with pytest.raises(gc.ErroIA):
            gc._extrair_json("sem json nenhum")

    def test_normaliza_categoria_hash_e_arroba(self):
        resumo, itens = gc._normalizar_resposta(
            {
                "resumo": "oi @maria",
                "itens": [
                    {
                        "categoria": "corrigido",
                        "texto": "Corrige   algo\ncom @joao",
                        "commits": ["bbb2222ffff", "zzz9999", "bbb2222"],
                    },
                    {"categoria": "Inventada", "texto": "Outro", "commits": []},
                    {"categoria": "Adicionado", "texto": "  "},
                    "lixo",
                ],
            },
            ["aaa1111", "bbb2222"],
        )
        assert "@\u200bmaria" in resumo
        assert len(itens) == 2
        assert itens[0]["categoria"] == "Corrigido"
        assert itens[0]["commits"] == ["bbb2222"]  # sem duplicata, sem hash inventado
        assert itens[0]["texto"] == "Corrige algo com @\u200bjoao"
        assert itens[1]["categoria"] == "Modificado"

    def test_itens_que_nao_sao_lista_viram_vazio(self):
        assert gc._normalizar_resposta({"itens": "x"}, ["a"]) == ("", [])


# --- geração com IA ----------------------------------------------------------


class TestGerarComIA:
    def test_usa_a_descricao_da_ia_com_links_e_resumo(self, monkeypatch):
        def falso(mensagens, config, timeout=120.0):
            usuario = mensagens[-1]["content"]
            assert "### Commit aaa1111" in usuario
            assert "Bump version" not in usuario  # ruído nem chega na IA
            assert "+nova linha" in usuario  # o diff vai junto
            return _resposta(
                [
                    {
                        "categoria": "Adicionado",
                        "texto": "Nova flag `--x` que faz tal coisa.",
                        "commits": ["aaa1111"],
                    },
                    {
                        "categoria": "Corrigido",
                        "texto": "Corrige o bug y.",
                        "commits": ["bbb2222"],
                    },
                ],
                resumo="Versão pequena.",
            )

        monkeypatch.setattr(gc, "_chamar_modelo", falso)
        saida = gc.gerar_changelog(
            "v1", "v2", ia=_config(), data_lancamento="2026-01-01"
        )

        assert saida.startswith("## [v2] - 2026-01-01")
        assert "> Versão pequena." in saida
        assert "- Nova flag `--x` que faz tal coisa." in saida
        assert "[`aaa1111`](https://github.com/o/r/commit/aaa1111)" in saida
        assert "### Adicionado" in saida and "### Corrigido" in saida
        assert "feat: nova flag --x" not in saida  # título cru não aparece
        assert f"Modo: IA ({gc.MODELO_PADRAO})" in saida

    def test_commit_esquecido_pela_ia_entra_pelo_titulo(self, monkeypatch):
        monkeypatch.setattr(
            gc,
            "_chamar_modelo",
            lambda m, c, timeout=120.0: _resposta(
                [
                    {
                        "categoria": "Adicionado",
                        "texto": "Só a flag.",
                        "commits": ["aaa1111"],
                    }
                ]
            ),
        )
        saida = gc.gerar_changelog("v1", "v2", ia=_config())
        assert "Só a flag." in saida
        assert "fix: corrige o bug y" in saida  # nada some do changelog

    def test_sem_ia_mantem_o_formato_antigo(self, monkeypatch):
        def proibido(*a, **k):
            raise AssertionError("não devia chamar a IA")

        monkeypatch.setattr(gc, "_chamar_modelo", proibido)
        saida = gc.gerar_changelog("v1", "v2", ia=None)
        assert "- feat: nova flag --x ([`aaa1111`]" in saida
        assert "  detalhe do corpo" in saida
        assert "Modo: títulos dos commits" in saida

    def test_resumido_nao_chama_ia_nem_mostra_links(self, monkeypatch):
        monkeypatch.setattr(
            gc, "_chamar_modelo", lambda *a, **k: pytest.fail("não devia chamar")
        )
        saida = gc.gerar_changelog("v1", "v2", detalhado=False, ia=_config())
        assert "- feat: nova flag --x (`aaa1111`)" in saida
        assert "modo resumido" in saida

    def test_ia_com_erro_fatal_cai_nos_titulos(self, monkeypatch):
        def negado(*a, **k):
            raise gc.ErroIA(403, "Models desativado na organização")

        monkeypatch.setattr(gc, "_chamar_modelo", negado)
        saida = gc.gerar_changelog("v1", "v2", ia=_config())
        assert "- feat: nova flag --x" in saida
        assert "IA indisponível: HTTP 403" in saida

    def test_exigir_ia_propaga_o_erro(self, monkeypatch):
        def negado(*a, **k):
            raise gc.ErroIA(403, "negado")

        monkeypatch.setattr(gc, "_chamar_modelo", negado)
        with pytest.raises(gc.ErroIA):
            gc.gerar_changelog("v1", "v2", ia=_config(), exigir_ia=True)

    def test_json_invalido_num_lote_usa_os_titulos_dele(self, monkeypatch):
        monkeypatch.setattr(gc, "_chamar_modelo", lambda *a, **k: "isto não é json")
        saida = gc.gerar_changelog("v1", "v2", ia=_config())
        assert "- feat: nova flag --x" in saida
        assert "IA indisponível" in saida
        assert "último erro: a IA não devolveu um JSON válido" in saida

    def test_comentario_html_do_rodape_nao_quebra_com_dois_hifens(self, monkeypatch):
        def negado(*a, **k):
            raise gc.ErroIA(None, "erro --> estranho")

        monkeypatch.setattr(gc, "_chamar_modelo", negado)
        saida = gc.gerar_changelog("v1", "v2", ia=_config())
        rodape = saida[saida.rindex("<!--") :]
        assert rodape.count("-->") == 1


class TestLotes:
    def test_commits_grandes_viram_varios_lotes_e_sao_unidos(self, monkeypatch):
        monkeypatch.setattr(gc, "_diff_do_commit", lambda h: DIFF_EXEMPLO * 30)
        chamadas = []

        def falso(mensagens, config, timeout=120.0):
            sistema = mensagens[0]["content"]
            usuario = mensagens[-1]["content"]
            chamadas.append(sistema[:20])
            if sistema == gc.PROMPT_CONSOLIDAR:
                dados = json.loads(usuario)
                return _resposta(dados["itens"], resumo="Resumo final.")
            hashes = re.findall(r"### Commit (\w+)", usuario)
            return _resposta(
                [
                    {"categoria": "Adicionado", "texto": f"Item {h}.", "commits": [h]}
                    for h in hashes
                ],
                resumo="Parcial.",
            )

        monkeypatch.setattr(gc, "_chamar_modelo", falso)
        # Limite pequeno: cada commit enche um lote sozinho.
        saida = gc.gerar_changelog("v1", "v2", ia=_config(limite_prompt=2500))

        assert len(chamadas) == 3  # 2 lotes + 1 de união
        assert "> Resumo final." in saida
        assert "Item aaa1111." in saida and "Item bbb2222." in saida

    def test_uniao_que_perde_commit_mantem_itens_originais(self, monkeypatch):
        monkeypatch.setattr(gc, "_diff_do_commit", lambda h: DIFF_EXEMPLO * 30)

        def falso(mensagens, config, timeout=120.0):
            if mensagens[0]["content"] == gc.PROMPT_CONSOLIDAR:
                return _resposta(
                    [
                        {
                            "categoria": "Adicionado",
                            "texto": "Só um.",
                            "commits": ["aaa1111"],
                        }
                    ],
                    resumo="Resumo.",
                )
            hashes = re.findall(r"### Commit (\w+)", mensagens[-1]["content"])
            return _resposta(
                [
                    {"categoria": "Adicionado", "texto": f"Item {h}.", "commits": [h]}
                    for h in hashes
                ]
            )

        monkeypatch.setattr(gc, "_chamar_modelo", falso)
        saida = gc.gerar_changelog("v1", "v2", ia=_config(limite_prompt=2500))
        assert "Item aaa1111." in saida and "Item bbb2222." in saida
        assert "Só um." not in saida

    def test_erro_413_divide_o_lote_ao_meio(self, monkeypatch):
        tamanhos = []

        def falso(mensagens, config, timeout=120.0):
            usuario = mensagens[-1]["content"]
            hashes = re.findall(r"### Commit (\w+)", usuario)
            tamanhos.append(len(hashes))
            if len(hashes) > 1:
                raise gc.ErroIA(413, "tokens_limit_reached")
            return _resposta(
                [
                    {
                        "categoria": "Adicionado",
                        "texto": f"Item {hashes[0]}.",
                        "commits": hashes,
                    }
                ]
            )

        monkeypatch.setattr(gc, "_chamar_modelo", falso)
        saida = gc.gerar_changelog("v1", "v2", ia=_config())
        assert tamanhos == [2, 1, 1]
        assert "Item aaa1111." in saida and "Item bbb2222." in saida


# --- chamada HTTP ------------------------------------------------------------


class _RespostaHTTP:
    status = 200
    headers = {"Content-Type": "application/json"}
    url = "https://models.github.ai/inference/chat/completions"

    def __init__(self, dados):
        self._bruto = dados if isinstance(dados, bytes) else json.dumps(dados).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._bruto


class TestChamarModelo:
    def test_monta_pedido_e_le_a_resposta(self, monkeypatch):
        vistos = {}

        def falso(pedido, timeout=0):
            vistos["url"] = pedido.full_url
            vistos["auth"] = pedido.get_header("Authorization")
            vistos["corpo"] = json.loads(pedido.data)
            return _RespostaHTTP({"choices": [{"message": {"content": "{}"}}]})

        monkeypatch.setattr(gc.urllib.request, "urlopen", falso)
        texto = gc._chamar_modelo(
            [{"role": "user", "content": "oi"}], gc.ConfigIA(token="abc", modelo="m/x")
        )
        assert texto == "{}"
        assert vistos["url"] == gc.GEMINI_BASE_URL.rstrip("/") + "/chat/completions"
        assert vistos["auth"] == "Bearer abc"
        assert vistos["corpo"]["model"] == "m/x"

    def test_erro_http_vira_erro_ia_com_codigo(self, monkeypatch):
        import io
        import urllib.error

        def falso(pedido, timeout=0):
            raise urllib.error.HTTPError(
                gc.GEMINI_BASE_URL, 403, "Forbidden", {}, io.BytesIO(b"sem permissao")
            )

        monkeypatch.setattr(gc.urllib.request, "urlopen", falso)
        with pytest.raises(gc.ErroIA) as info:
            gc._chamar_modelo([], gc.ConfigIA(token="t"))
        assert info.value.codigo == 403 and info.value.fatal

    def test_corpo_vazio_mostra_o_que_veio_e_tenta_de_novo(self, monkeypatch):
        tentativas = []

        def falso(pedido, timeout=0):
            tentativas.append(1)
            return _RespostaHTTP(b"")

        monkeypatch.setattr(gc.urllib.request, "urlopen", falso)
        monkeypatch.setattr(gc.time, "sleep", lambda s: None)
        with pytest.raises(gc.ErroIA) as info:
            gc._chamar_modelo([], gc.ConfigIA(token="t"))
        assert len(tentativas) == gc.MAX_TENTATIVAS
        assert "não é JSON" in info.value.detalhe
        assert "status 200" in info.value.detalhe and "0 bytes" in info.value.detalhe

    def test_corpo_html_aparece_na_mensagem(self, monkeypatch):
        monkeypatch.setattr(
            gc.urllib.request,
            "urlopen",
            lambda pedido, timeout=0: _RespostaHTTP(b"<html>Not Found</html>"),
        )
        monkeypatch.setattr(gc.time, "sleep", lambda s: None)
        with pytest.raises(gc.ErroIA) as info:
            gc._chamar_modelo([], gc.ConfigIA(token="t"))
        assert "<html>Not Found</html>" in info.value.detalhe

    def test_429_espera_e_tenta_de_novo(self, monkeypatch):
        import io
        import urllib.error

        tentativas = []

        def falso(pedido, timeout=0):
            tentativas.append(1)
            if len(tentativas) == 1:
                raise urllib.error.HTTPError(
                    gc.GEMINI_BASE_URL,
                    429,
                    "Too Many",
                    {"Retry-After": "2"},
                    io.BytesIO(b""),
                )
            return _RespostaHTTP({"choices": [{"message": {"content": "ok"}}]})

        esperas = []
        monkeypatch.setattr(gc.urllib.request, "urlopen", falso)
        monkeypatch.setattr(gc.time, "sleep", esperas.append)
        assert gc._chamar_modelo([], gc.ConfigIA(token="t")) == "ok"
        assert len(tentativas) == 2 and esperas == [2.0]


# --- git de verdade ----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")
def test_com_git_real_ignora_arquivos_de_ruido_e_monta_lista(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "app.py").write_text("print('v1')\n")
    git("add", ".")
    git("commit", "-q", "-m", "inicial")
    git("tag", "v1")

    (tmp_path / "app.py").write_text("print('v2')\nprint('novo')\n")
    (tmp_path / "CHANGELOG.md").write_text("ruído\n")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "nodeids").write_text("x\n" * 50)
    git("add", ".")
    git("commit", "-q", "-m", "feat: mostra algo novo")

    monkeypatch.chdir(tmp_path)
    gc_real = _carregar()
    monkeypatch.setattr(gc_real, "PAUSA_ENTRE_LOTES", 0)

    vistos = {}

    def falso(mensagens, config, timeout=120.0):
        vistos["prompt"] = mensagens[-1]["content"]
        h = re.search(r"### Commit (\w+)", vistos["prompt"]).group(1)
        return _resposta(
            [{"categoria": "Adicionado", "texto": "Mostra algo novo.", "commits": [h]}]
        )

    monkeypatch.setattr(gc_real, "_chamar_modelo", falso)
    saida = gc_real.gerar_changelog("v1", "HEAD", ia=gc_real.ConfigIA(token="t"))

    assert "+print('novo')" in vistos["prompt"]
    assert "CHANGELOG.md" not in vistos["prompt"]
    assert "nodeids" not in vistos["prompt"]
    assert "- Mostra algo novo." in saida
    assert "<summary>Arquivos alterados (1)" in saida
    assert "- `app.py` (+2 −1)" in saida
