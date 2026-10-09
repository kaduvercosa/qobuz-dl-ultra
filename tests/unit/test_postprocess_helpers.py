"""Testa os helpers puros de `qobuz_dl/postprocess.py`.

`test_postprocess.py` cobre o ciclo init -> update -> finalize do relatório
(.report.json). Estes testes travam as peças de baixo nível que ele usa:
conversão de artistas, normalização de status, ordenação de faixas, cálculo do
resumo, o cabeçalho "Vários Artistas"/"Diversos", o formato legado e os dois
arquivos da coleção (índice JSONL e relatório Markdown).
"""

import asyncio
import json
import os

import pytest

from qobuz_dl import postprocess as pp

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Conversões simples
# ---------------------------------------------------------------------------
class TestFormatarArtistas:
    @pytest.mark.parametrize(
        "valor, esperado",
        [
            (None, ""),
            ("  Artista  ", "Artista"),
            (["A", " B ", "", "  "], "A, B"),
            (("A", "B"), "A, B"),
            ([], ""),
            (123, "123"),
        ],
    )
    def test_formas_de_entrada(self, valor, esperado):
        assert pp._formatar_artistas(valor) == esperado

    def test_set_de_um_elemento(self):
        assert pp._formatar_artistas({"Solo"}) == "Solo"


class TestListaArtistas:
    @pytest.mark.parametrize(
        "valor, esperado",
        [
            (None, []),
            ("", []),
            ("   ", []),
            ("A", ["A"]),
            ("A, B ,, C", ["A", "B", "C"]),
            (["A", " ", "B"], ["A", "B"]),
            (("X",), ["X"]),
        ],
    )
    def test_formas_de_entrada(self, valor, esperado):
        assert pp._lista_artistas(valor) == esperado


class TestNormalizarStatus:
    @pytest.mark.parametrize(
        "status, esperado",
        [
            ("ok", "concluido"),
            ("falha", "falha"),
            ("pulada", "pulada"),
            ("pendente", "pendente"),
            ("", "pendente"),
            (None, "pendente"),
        ],
    )
    def test_status(self, status, esperado):
        assert pp._normalizar_status(status) == esperado


class TestNormId:
    @pytest.mark.parametrize(
        "valor, esperado",
        [(None, None), ("", None), ("  ", None), (5, "5"), (" 7 ", "7"), (1.5, "1.5")],
    )
    def test_ids(self, valor, esperado):
        assert pp._norm_id(valor) == esperado


class TestTrackSortKey:
    def test_numeros_ordenam_numericamente_nao_como_texto(self):
        faixas = [{"numero": 10}, {"numero": 2}, {"numero": 1}]
        assert [f["numero"] for f in sorted(faixas, key=pp._track_sort_key)] == [1, 2, 10]

    def test_numero_em_texto_vira_inteiro(self):
        assert pp._track_sort_key({"numero": "03"}) == (0, 3)

    def test_sem_numero_vai_para_o_fim(self):
        faixas = [{"numero": None}, {"numero": 1}, {}]
        ordenadas = sorted(faixas, key=pp._track_sort_key)
        assert ordenadas[0] == {"numero": 1}

    def test_numero_nao_numerico_vai_depois_dos_numericos(self):
        faixas = [{"numero": "A1"}, {"numero": 9}]
        assert sorted(faixas, key=pp._track_sort_key)[0] == {"numero": 9}
        assert pp._track_sort_key({"numero": "A1"}) == (1, "A1")


class TestOrdenar:
    def test_chaves_conhecidas_primeiro_na_ordem_dada(self):
        r = pp._ordenar({"c": 3, "a": 1, "b": 2}, ["a", "b", "c"])
        assert list(r) == ["a", "b", "c"]

    def test_chaves_extras_ficam_no_final(self):
        r = pp._ordenar({"extra": 0, "b": 2, "a": 1}, ["a", "b"])
        assert list(r) == ["a", "b", "extra"]

    def test_ordem_com_chave_ausente_e_ignorada(self):
        assert pp._ordenar({"a": 1}, ["x", "a"]) == {"a": 1}


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------
class TestSkeleton:
    def test_esqueleto_basico(self):
        r = pp._skeleton("album", "Disco", ["A", "B"], "42", {"url": "u", "upc": "9"},
                         {"formato": "FLAC", "bit_depth": 24}, "Album")
        assert r["tipo"] == "album"
        assert r["identificacao"] == {
            "titulo": "Disco", "artista": "A, B", "tipo_lancamento": "Album",
            "id": "42", "upc": "9", "url": "u",
        }
        assert r["qualidade"]["formato"] == "FLAC"
        assert r["qualidade"]["bit_depth"] == 24
        assert r["qualidade"]["alvo_atingida"] is None
        assert r["progresso"]["estado"]["situacao"] == "em_andamento"
        assert r["progresso"]["estado"]["verificado"] is False
        assert r["faixas"] == []

    def test_upc_explicito_vence_o_do_extra(self):
        r = pp._skeleton("album", "D", "A", "1", {"upc": "extra"}, None, upc="explicito")
        assert r["identificacao"]["upc"] == "explicito"

    def test_entradas_none(self):
        r = pp._skeleton("faixa", None, None, None, None, None)
        assert r["identificacao"]["titulo"] == ""
        assert r["identificacao"]["id"] == ""
        assert r["extra"] == {}


class TestCriarFaixaPendente:
    def test_faixa_completa(self):
        r = pp._criar_faixa_pendente({
            "numero": 3, "id": 77, "titulo": "Musica",
            "artista": "A, B", "artista_album": ["A"], "album": "Disco",
            "tipo_lancamento": "EP", "isrc": "X1", "compositor": ["C1", "C2"],
        })
        assert r["numero"] == 3 and r["id"] == "77"
        ident = r["identificacao"]
        assert ident["main_artists"] == ["A", "B"]
        assert ident["artista"] == "A, B"
        assert ident["artista_album"] == "A"
        assert ident["compositor"] == "C1, C2"
        assert r["download"] == {
            "situacao": "pendente", "motivo": "", "checksum": None, "atualizado_em": None,
        }
        assert r["letras"] == {}

    def test_faixa_vazia_usa_defaults(self):
        r = pp._criar_faixa_pendente({})
        assert r["id"] is None and r["numero"] is None
        assert r["identificacao"]["titulo"] == "Faixa"
        assert r["identificacao"]["main_artists"] == []


class TestGarantirEstrutura:
    def test_item_vazio_ganha_todos_os_campos(self):
        r = {}
        pp._garantir_estrutura_item(r)
        assert r["tipo"] == "faixa"
        for chave in ("titulo", "artista", "tipo_lancamento", "id", "upc", "url"):
            assert r["identificacao"][chave] == ""
        assert r["qualidade"] == {
            "formato": "", "bit_depth": None, "sampling_rate": None, "alvo_atingida": None,
        }
        assert r["progresso"]["estado"]["situacao"] == "em_andamento"
        assert r["progresso"]["resumo"] == {}
        assert r["faixas"] == []

    def test_nao_sobrescreve_o_que_ja_existe(self):
        r = {"tipo": "album", "identificacao": {"titulo": "X"},
             "qualidade": {"formato": "MP3"},
             "progresso": {"estado": {"situacao": "completo"}}}
        pp._garantir_estrutura_item(r)
        assert r["tipo"] == "album"
        assert r["identificacao"]["titulo"] == "X"
        assert r["qualidade"]["formato"] == "MP3"
        assert r["progresso"]["estado"]["situacao"] == "completo"

    def test_faixa_vazia_ganha_estrutura(self):
        f = {}
        pp._garantir_estrutura_faixa(f)
        assert f["identificacao"]["titulo"] == "Faixa"
        assert f["download"]["situacao"] == "pendente"
        assert f["letras"] == {}

    def test_faixa_preserva_situacao_existente(self):
        f = {"download": {"situacao": "concluido"}}
        pp._garantir_estrutura_faixa(f)
        assert f["download"]["situacao"] == "concluido"


# ---------------------------------------------------------------------------
# Resumo / progresso
# ---------------------------------------------------------------------------
def _faixa(situacao, **ident):
    return {"download": {"situacao": situacao}, "identificacao": dict(ident)}


class TestRecalcResumo:
    def test_conta_cada_situacao(self):
        r = {"faixas": [
            _faixa("concluido"), _faixa("concluido"), _faixa("pulada"),
            _faixa("falha"), _faixa("pendente"), _faixa("pendente"),
        ]}
        pp._recalc_resumo(r)
        assert r["progresso"]["resumo"] == {
            "total": 6, "concluidas": 2, "puladas": 1, "falhas": 1, "pendentes": 2,
        }

    def test_sem_faixas_zera_tudo(self):
        r = {}
        pp._recalc_resumo(r)
        assert r["progresso"]["resumo"]["total"] == 0

    def test_faixa_sem_download_nao_conta_em_nenhuma_categoria(self):
        r = {"faixas": [{}]}
        pp._recalc_resumo(r)
        resumo = r["progresso"]["resumo"]
        assert resumo["total"] == 1
        assert resumo["concluidas"] == resumo["falhas"] == resumo["pendentes"] == 0


def test_atualizar_progresso_muda_so_o_atualizado_em(monkeypatch):
    r = {}
    pp._garantir_estrutura_item(r)
    criado = r["progresso"]["estado"]["criado_em"]
    monkeypatch.setattr(pp, "_now_iso", lambda: "2030-01-01T00:00:00+00:00")
    pp._atualizar_progresso_item(r)
    assert r["progresso"]["estado"]["atualizado_em"] == "2030-01-01T00:00:00+00:00"
    assert r["progresso"]["estado"]["criado_em"] == criado


class TestCabecalhoDinamico:
    @staticmethod
    def _relatorio(tipo, faixas):
        r = {"tipo": tipo, "identificacao": {"artista": "", "tipo_lancamento": ""},
             "faixas": faixas}
        return r

    def test_album_nunca_e_recalculado(self):
        r = self._relatorio("album", [_faixa("ok", artista_album="Outro", tipo_lancamento="EP")])
        r["identificacao"]["artista"] = "Original"
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Original"
        assert r["identificacao"]["tipo_lancamento"] == ""

    def test_um_unico_artista_vira_o_artista_do_cabecalho(self):
        r = self._relatorio("playlist", [
            _faixa("ok", artista_album="Banda", artista="Banda, Convidado"),
            _faixa("ok", artista_album="Banda", artista="Banda"),
        ])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Banda"

    def test_artista_da_faixa_so_e_fallback(self):
        # feat. pontual no performer NÃO pode gerar "Vários Artistas".
        r = self._relatorio("faixa", [
            _faixa("ok", artista_album="Banda", artista="Banda, Convidado"),
            _faixa("ok", artista="Banda"),
        ])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Banda"

    def test_varios_artistas(self):
        r = self._relatorio("playlist", [
            _faixa("ok", artista_album="A"), _faixa("ok", artista_album="B"),
        ])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Vários Artistas"

    def test_tipo_de_lancamento_unico(self):
        r = self._relatorio("playlist", [
            _faixa("ok", tipo_lancamento="Single"), _faixa("ok", tipo_lancamento="Single"),
        ])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["tipo_lancamento"] == "Single"

    def test_tipos_diferentes_viram_diversos(self):
        r = self._relatorio("playlist", [
            _faixa("ok", tipo_lancamento="Single"), _faixa("ok", tipo_lancamento="EP"),
        ])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["tipo_lancamento"] == "Diversos"

    def test_sem_dados_mantem_o_cabecalho(self):
        r = self._relatorio("faixa", [_faixa("ok")])
        r["identificacao"]["artista"] = "Antigo"
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Antigo"

    def test_converge_quando_chega_artista_diferente_depois(self):
        r = self._relatorio("playlist", [_faixa("ok", artista_album="A")])
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "A"
        r["faixas"].append(_faixa("ok", artista_album="B"))
        pp._recalc_cabecalho_dinamico(r)
        assert r["identificacao"]["artista"] == "Vários Artistas"


class TestCompatibilidadeLegada:
    def test_aliases_planos_do_item(self):
        r = {
            "identificacao": {"titulo": "T", "artista": "A", "id": "9"},
            "progresso": {"estado": {"situacao": "completo"}, "resumo": {"concluidas": 4, "total": 5}},
            "qualidade": {"alvo_atingida": True},
            "faixas": [],
        }
        pp._aplicar_compatibilidade_legada(r)
        assert (r["titulo"], r["artista"], r["id"]) == ("T", "A", "9")
        assert r["estado"] == "completo"
        assert r["resumo"]["baixadas"] == 4 and r["resumo"]["total"] == 5
        assert r["qualidade_atingida"] is True

    def test_relatorio_vazio_usa_defaults(self):
        r = {}
        pp._aplicar_compatibilidade_legada(r)
        assert r["estado"] == "em_andamento"
        assert r["resumo"] == {"baixadas": 0}
        assert r["qualidade_atingida"] is None

    def test_status_legado_das_faixas(self):
        r = {"faixas": [
            {"download": {"situacao": "concluido", "motivo": ""},
             "identificacao": {"isrc": "X", "titulo": "M", "artista": "A", "compositor": "C"}},
            {"download": {"situacao": "falha", "motivo": "erro de rede"}},
            {},
        ]}
        pp._aplicar_compatibilidade_legada(r)
        f1, f2, f3 = r["faixas"]
        assert f1["status"] == "ok" and f1["isrc"] == "X" and f1["compositor"] == "C"
        assert (f2["status"], f2["motivo"]) == ("falha", "erro de rede")
        assert f3["status"] == "pendente"

    def test_resumo_original_nao_e_alterado(self):
        r = {"progresso": {"resumo": {"concluidas": 1}}}
        pp._aplicar_compatibilidade_legada(r)
        assert "baixadas" not in r["progresso"]["resumo"]


# ---------------------------------------------------------------------------
# Leitura/escrita
# ---------------------------------------------------------------------------
class TestLoadReport:
    def test_arquivo_inexistente(self, tmp_path):
        assert pp._load_report(str(tmp_path / "nao.json")) == {}

    def test_json_valido(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        assert pp._load_report(str(p)) == {"a": 1}

    def test_json_quebrado_vira_vazio(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text("{quebrado", encoding="utf-8")
        assert pp._load_report(str(p)) == {}

    def test_json_que_nao_e_objeto_vira_vazio(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text("[1, 2]", encoding="utf-8")
        assert pp._load_report(str(p)) == {}


class TestAtomicWriteJson:
    def test_grava_utf8_sem_escapar_acentos(self, tmp_path):
        p = tmp_path / "sub" / "r.json"
        pp._atomic_write_json(str(p), {"titulo": "Coração"})
        texto = p.read_text(encoding="utf-8")
        assert "Coração" in texto and texto.endswith("\n")
        assert json.loads(texto) == {"titulo": "Coração"}

    def test_cria_pastas_e_nao_deixa_tmp(self, tmp_path):
        p = tmp_path / "a" / "b" / "r.json"
        pp._atomic_write_json(str(p), {})
        assert p.exists() and not os.path.exists(str(p) + ".tmp")

    def test_substitui_arquivo_existente(self, tmp_path):
        p = tmp_path / "r.json"
        pp._atomic_write_json(str(p), {"v": 1})
        pp._atomic_write_json(str(p), {"v": 2})
        assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}


def test_now_iso_tem_offset_de_fuso():
    from datetime import datetime

    agora = datetime.fromisoformat(pp._now_iso())
    assert agora.tzinfo is not None


class TestLockPorArquivo:
    async def test_mesmo_caminho_mesmo_lock(self):
        a = await pp._get_lock("/x/.report.json")
        b = await pp._get_lock("/x/.report.json")
        assert a is b

    async def test_caminhos_diferentes_locks_diferentes(self):
        assert await pp._get_lock("/x/1") is not await pp._get_lock("/x/2")

    async def test_lock_serializa_acessos(self):
        lock = await pp._get_lock("/x/serial")
        ordem = []

        async def trabalho(nome):
            async with lock:
                ordem.append(f"{nome}-inicio")
                await asyncio.sleep(0)
                ordem.append(f"{nome}-fim")

        await asyncio.gather(trabalho("a"), trabalho("b"))
        assert ordem in (
            ["a-inicio", "a-fim", "b-inicio", "b-fim"],
            ["b-inicio", "b-fim", "a-inicio", "a-fim"],
        )


# ---------------------------------------------------------------------------
# Arquivos da coleção
# ---------------------------------------------------------------------------
class TestIndiceDaColecao:
    def test_acrescenta_uma_linha_jsonl_por_album(self, tmp_path):
        db = str(tmp_path / "qobuz_dl.db")
        for i in (1, 2):
            pp.generate_index_entry(
                db, f"id{i}", f"Álbum {i}", "Artista", str(tmp_path / f"a{i}"),
                "FLAC", 24, 96.0, "2025-01-01", f"https://x/{i}",
            )
        linhas = (tmp_path / "collection_index.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(linhas) == 2
        primeira = json.loads(linhas[0])
        assert primeira["album_id"] == "id1"
        assert primeira["album_titulo"] == "Álbum 1"
        assert primeira["bit_depth"] == 24 and primeira["sampling_rate"] == 96.0
        assert os.path.isabs(primeira["caminho"])
        assert "gerado_em" in primeira

    def test_nao_escapa_acentos(self, tmp_path):
        pp.generate_index_entry(
            str(tmp_path / "db"), "1", "Coração", "A", "x", "FLAC", 16, 44.1, "", "",
        )
        assert "Coração" in (tmp_path / "collection_index.jsonl").read_text(encoding="utf-8")


class TestRelatorioDaColecao:
    def _stats(self, **extra):
        base = {
            "albums": 3, "tracks": 30, "hires": 2, "flac": 28, "mp3": 2,
            "quality_met": 25, "quality_not_met": 5,
            "formats": {"FLAC": 28, "MP3": 2},
            "bit_depths": {"24": 2, "16": 1},
            "sample_rates": {"96.0": 2},
            "top_artists": [(f"Artista {i}", 15 - i) for i in range(12)],
            "oldest": "1999-01-01", "newest": "2026-01-01",
        }
        base.update(extra)
        return base

    def test_conteudo_completo(self, tmp_path):
        pp.update_collection_report(str(tmp_path / "db"), self._stats())
        md = (tmp_path / "collection_report.md").read_text(encoding="utf-8")
        assert md.startswith("# Relatorio da Colecao")
        assert "- Albuns: 3" in md and "- Faixas: 30" in md
        assert "- Hi-Res (>=24bit): 2" in md
        assert "- FLAC: 28" in md and "- MP3: 2" in md
        assert "- Qualidade atingida: 25" in md
        assert "- Qualidade nao atingida: 5" in md
        assert "## Formatos" in md and "## Bit Depths" in md and "## Sample Rates" in md
        assert "- 24: 2" in md and "- 96.0: 2" in md
        assert "- Mais antigo: 1999-01-01" in md
        assert "- Mais recente: 2026-01-01" in md

    def test_top_artistas_limitado_a_dez(self, tmp_path):
        pp.update_collection_report(str(tmp_path / "db"), self._stats())
        md = (tmp_path / "collection_report.md").read_text(encoding="utf-8")
        assert "- Artista 9:" in md
        assert "Artista 10" not in md

    def test_sem_periodo_nao_cria_a_secao(self, tmp_path):
        pp.update_collection_report(
            str(tmp_path / "db"), self._stats(oldest=None, newest=None)
        )
        assert "## Periodo" not in (tmp_path / "collection_report.md").read_text(encoding="utf-8")

    def test_so_um_dos_extremos(self, tmp_path):
        pp.update_collection_report(str(tmp_path / "db"), self._stats(oldest=None))
        md = (tmp_path / "collection_report.md").read_text(encoding="utf-8")
        assert "Mais antigo" not in md and "Mais recente" in md

    def test_stats_vazio_usa_zeros(self, tmp_path):
        pp.update_collection_report(str(tmp_path / "db"), {})
        md = (tmp_path / "collection_report.md").read_text(encoding="utf-8")
        assert "- Albuns: 0" in md and "- Faixas: 0" in md

    def test_relatorios_sao_acrescentados_nao_sobrescritos(self, tmp_path):
        db = str(tmp_path / "db")
        pp.update_collection_report(db, {})
        pp.update_collection_report(db, {})
        md = (tmp_path / "collection_report.md").read_text(encoding="utf-8")
        assert md.count("# Relatorio da Colecao") == 2
