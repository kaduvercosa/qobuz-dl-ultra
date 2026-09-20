"""Testes para postprocess.py -- o sistema de report.json incremental.

POR QUE ESTE ARQUIVO EXISTIA E ERA ZERO
----------------------------------------
O postprocess.py foi reescrito do zero durante esta sessao: saiu de um
conjunto de funcoes sync (generate_album_log, generate_credits etc.) para
uma API assincrona com lock por pasta, upsert por id, ordenacao por numero
e promocao de cabecalho por prioridade de tipo. Um modulo de 820 linhas
que escreve em disco durante downloads paralelos, sem um unico teste.

BUGS QUE ESTES TESTES TRAVAM
-----------------------------
1. DUPLICATA DE FAIXA (o bug que motivou este arquivo): baixar uma faixa
   avulsa e depois o album completo criava duas entradas para a mesma faixa
   quando o ID vinha como int em um caso e str no outro. A causa: o upsert
   comparava sem normalizar o tipo. O _norm_id() resolve isso, e estes
   testes verificam que a comparacao str(id) == str(id) funciona nos dois
   sentidos (int->str e str->int).

2. REGRESSAO DE ORDEM: o asyncio.gather nao garante ordem de conclusao.
   Estes testes verificam que a lista de faixas no JSON final e sempre
   ordenada pelo campo "numero", independente da ordem de chamada.

3. PROMOCAO DE TIPO REGRESSIVA: baixar o album completo depois de uma
   faixa avulsa tem que atualizar o cabecalho (faixa->album). O caminho
   inverso (album->faixa) nao pode rebaixar. Ambos sao verificados.

4. RETOMADA SEM RESET: chamadas repetidas de init_report() no mesmo
   diretorio (ex.: script rodado duas vezes sem apagar a pasta) nao devem
   zerar faixas ja com status "ok". Verificado.

5. ESCRITA ATOMICA: o arquivo intermediario .tmp deve ser apagado apos
   a escrita. Verificado indiretamente pela ausencia do arquivo .tmp.
"""

import asyncio
import json
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers de carregamento isolados do modulo real
# ---------------------------------------------------------------------------


def _ler(dirn: str) -> dict:
    """Le o report.json de um diretorio de teste."""
    path = os.path.join(dirn, ".report.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _ids(report: dict) -> list:
    return [f["id"] for f in report["faixas"]]


def _status(report: dict) -> dict:
    return {f["id"]: f["status"] for f in report["faixas"]}


def _numeros(report: dict) -> list:
    return [f["numero"] for f in report["faixas"]]


# ---------------------------------------------------------------------------
# Fixture: importa o modulo real do projeto
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pp():
    """Importa postprocess garantindo que os locks async sao limpos entre
    sessoes de teste (o dicionario _locks e' global no modulo)."""
    from qobuz_dl import postprocess

    return postprocess


@pytest.fixture(autouse=True)
def limpar_locks(pp):
    """Limpa o dicionario de locks entre cada teste para evitar que locks
    de testes anteriores bloqueiem o proximo."""
    pp._locks.clear()
    yield
    pp._locks.clear()


# ===========================================================================
# _norm_id: normalizacao de tipo de ID
# ===========================================================================


class TestNormId:
    """O bug de duplicata nasceu aqui: int != str em Python."""

    def test_int_e_str_do_mesmo_numero_normalizam_igual(self, pp):
        assert pp._norm_id(257946874) == pp._norm_id("257946874")

    def test_none_vira_none(self, pp):
        assert pp._norm_id(None) is None

    def test_string_vazia_vira_none(self, pp):
        assert pp._norm_id("") is None
        assert pp._norm_id("   ") is None

    def test_float_e_aceito(self, pp):
        assert pp._norm_id(1.0) == "1.0"

    def test_preserva_texto_de_id_nao_numerico(self, pp):
        assert pp._norm_id("bqexzyruqy65a") == "bqexzyruqy65a"


# ===========================================================================
# init_report: criacao e promocao de cabecalho
# ===========================================================================


class TestInitReport:
    def test_cria_arquivo_em_pasta_nova(self, pp, tmp_path):
        asyncio.run(pp.init_report(str(tmp_path), tipo="faixa", titulo="Single X"))
        assert (tmp_path / ".report.json").exists()

    def test_cabecalho_inicial_correto(self, pp, tmp_path):
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Album Y",
                artista="Artista Z",
                item_id="abc123",
                extra={"genero": "Jazz", "upc": "000"},
                qualidade={"formato": "FLAC", "bit_depth": 24},
            )
        )
        r = _ler(str(tmp_path))
        assert r["tipo"] == "album"
        assert r["titulo"] == "Album Y"
        assert r["artista"] == "Artista Z"
        assert r["id"] == "abc123"
        assert r["extra"]["genero"] == "Jazz"
        assert r["qualidade"]["bit_depth"] == 24
        assert r["estado"] == "em_andamento"

    def test_faixas_previstas_populam_lista_como_pendente(self, pp, tmp_path):
        faixas = [
            {"numero": "01", "id": "t1", "titulo": "Intro", "artista": "A"},
            {"numero": "02", "id": "t2", "titulo": "Main", "artista": "A"},
            {"numero": "03", "id": "t3", "titulo": "Outro", "artista": "A"},
        ]
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Album",
                faixas_previstas=faixas,
            )
        )
        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 3
        assert all(f["status"] in ("pendente", "aguardando") for f in r["faixas"])

    def test_faixas_previstas_mantem_ordem_numerica(self, pp, tmp_path):
        faixas = [
            {"numero": "03", "id": "t3", "titulo": "C"},
            {"numero": "01", "id": "t1", "titulo": "A"},
            {"numero": "02", "id": "t2", "titulo": "B"},
        ]
        asyncio.run(
            pp.init_report(
                str(tmp_path), tipo="album", titulo="X", faixas_previstas=faixas
            )
        )
        r = _ler(str(tmp_path))
        ids = _ids(r)
        assert ids == ["t1", "t2", "t3"], f"ordem errada: {ids}"

    def test_idempotente_nao_apaga_progresso(self, pp, tmp_path):
        """Rodar duas vezes (retomada) nao deve resetar faixas ja concluidas."""
        faixas = [{"numero": "01", "id": "t1", "titulo": "X"}]
        asyncio.run(
            pp.init_report(
                str(tmp_path), tipo="album", titulo="A", faixas_previstas=faixas
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="X", status="ok"
            )
        )
        # segunda chamada de init (retomada)
        asyncio.run(
            pp.init_report(
                str(tmp_path), tipo="album", titulo="A", faixas_previstas=faixas
            )
        )
        r = _ler(str(tmp_path))
        assert _status(r)["t1"] == "ok", "progresso foi resetado!"

    def test_nao_duplica_faixa_ja_existente(self, pp, tmp_path):
        faixas = [{"numero": "01", "id": "t1", "titulo": "X"}]
        asyncio.run(
            pp.init_report(
                str(tmp_path), tipo="album", titulo="A", faixas_previstas=faixas
            )
        )
        asyncio.run(
            pp.init_report(
                str(tmp_path), tipo="album", titulo="A", faixas_previstas=faixas
            )
        )
        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 1

    # --- promocao de cabecalho ---

    def test_faixa_promovida_para_album(self, pp, tmp_path):
        """O cenario do bug: faixa avulsa primeiro, album completo depois."""
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="02",
                item_id="t2",
                titulo="Be Someone",
                status="ok",
                tipo_default="faixa",
                titulo_default="Album X",
            )
        )
        # album chega depois com init_report
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Album Completo",
                artista="A",
                item_id="alb1",
                faixas_previstas=[
                    {"numero": "01", "id": "t1", "titulo": "Intro"},
                    {"numero": "02", "id": "t2", "titulo": "Be Someone"},
                    {"numero": "03", "id": "t3", "titulo": "Outro"},
                ],
            )
        )
        r = _ler(str(tmp_path))
        assert r["tipo"] == "album"
        assert r["titulo"] == "Album Completo"

    def test_faixa_avulsa_nao_rebaixa_album(self, pp, tmp_path):
        """O caminho inverso: album ja existe, faixa avulsa chega depois."""
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Album Deluxe",
                artista="A",
                item_id="alb1",
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="02",
                item_id="t2",
                titulo="Be Someone",
                status="ok",
                tipo_default="faixa",
                titulo_default="faixa solta",
            )
        )
        r = _ler(str(tmp_path))
        assert r["tipo"] == "album", f"tipo regrediu para: {r['tipo']}"
        assert r["titulo"] == "Album Deluxe"

    def test_tmp_nao_sobra_apos_escrita(self, pp, tmp_path):
        asyncio.run(pp.init_report(str(tmp_path), tipo="faixa", titulo="X"))
        assert not (tmp_path / ".report.json.tmp").exists()


# ===========================================================================
# update_track_status: upsert correto e ordem
# ===========================================================================


class TestUpdateTrackStatus:
    def test_cria_report_quando_nao_existe(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="01",
                item_id="t1",
                titulo="Faixa",
                status="ok",
            )
        )
        assert (tmp_path / ".report.json").exists()

    def test_status_ok_registrado(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="01",
                item_id="t1",
                titulo="X",
                status="ok",
                artista="A",
            )
        )
        r = _ler(str(tmp_path))
        assert r["faixas"][0]["status"] == "ok"

    def test_status_falha_registrado(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="01",
                item_id="t1",
                titulo="X",
                status="falha",
                motivo="API timeout",
            )
        )
        r = _ler(str(tmp_path))
        faixa = r["faixas"][0]
        assert faixa["status"] == "falha"
        assert "timeout" in faixa.get("motivo", "").lower()

    def test_upsert_nao_duplica_mesma_faixa(self, pp, tmp_path):
        for status in ("ok", "ok"):
            asyncio.run(
                pp.update_track_status(
                    str(tmp_path),
                    numero="01",
                    item_id="t1",
                    titulo="X",
                    status=status,
                )
            )
        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 1

    def test_upsert_id_int_e_str_sao_a_mesma_faixa(self, pp, tmp_path):
        """O bug de duplicata: ID int na faixa avulsa, str no album."""
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="02",
                item_id="257946874",
                titulo="Be Someone",
                status="ok",
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="02",
                item_id=257946874,
                titulo="Be Someone",
                status="ok",
            )
        )
        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 1, (
            f"Bug de duplicata: {len(r['faixas'])} entradas para o mesmo ID"
        )

    def test_id_str_depois_de_int_via_init(self, pp, tmp_path):
        """Variante: init_report usa int (API JSON), update usa str (URL)."""
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="A",
                faixas_previstas=[
                    {"numero": "01", "id": 257946873, "titulo": "Intro"},
                    {"numero": "02", "id": 257946874, "titulo": "Be Someone"},
                ],
            )
        )
        # download_track recebe ID como string extraida da URL
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="02",
                item_id="257946874",
                titulo="Be Someone",
                status="ok",
            )
        )
        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 2, (
            f"Bug de duplicata: {len(r['faixas'])} faixas (esperado 2)"
        )
        assert _status(r)["257946874"] == "ok"

    def test_lista_sempre_ordenada_por_numero(self, pp, tmp_path):
        """As faixas chegam fora de ordem (download paralelo)."""
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="03", item_id="t3", titulo="C", status="ok"
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="A", status="ok"
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="02", item_id="t2", titulo="B", status="ok"
            )
        )
        r = _ler(str(tmp_path))
        ids = _ids(r)
        assert ids == ["t1", "t2", "t3"], f"ordem errada: {ids}"

    def test_resumo_e_recalculado(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="A", status="ok"
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="02", item_id="t2", titulo="B", status="falha"
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="03", item_id="t3", titulo="C", status="pulada"
            )
        )
        r = _ler(str(tmp_path))
        res = r["resumo"]
        assert res["total"] == 3
        assert res["baixadas"] == 1
        assert res["falhas"] == 1
        assert res["puladas"] == 1

    def test_campos_metalicos_preservados_no_upsert(self, pp, tmp_path):
        """Ao atualizar o status de uma faixa pre-populada por init_report,
        os campos ISRC/compositor/interpretes ja existentes nao devem sumir."""
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="A",
                faixas_previstas=[
                    {
                        "numero": "01",
                        "id": "t1",
                        "titulo": "X",
                        "isrc": "US123",
                        "compositor": "Mozart",
                        "interpretes": ["Pianista Y"],
                    }
                ],
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="01",
                item_id="t1",
                titulo="X",
                status="ok",
                # sem isrc/compositor: nao deve apagar o que ja estava
            )
        )
        r = _ler(str(tmp_path))
        faixa = r["faixas"][0]
        assert faixa.get("isrc") == "US123"
        assert faixa.get("compositor") == "Mozart"


# ===========================================================================
# finalize_report
# ===========================================================================


class TestFinalizeReport:
    def test_estado_completo(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="X", status="ok"
            )
        )
        asyncio.run(pp.finalize_report(str(tmp_path), completo=True))
        r = _ler(str(tmp_path))
        assert r["estado"] == "completo"

    def test_estado_incompleto(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="X", status="falha"
            )
        )
        asyncio.run(pp.finalize_report(str(tmp_path), completo=False))
        r = _ler(str(tmp_path))
        assert r["estado"] == "incompleto"

    def test_qualidade_atingida_registrada(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path), numero="01", item_id="t1", titulo="X", status="ok"
            )
        )
        asyncio.run(
            pp.finalize_report(str(tmp_path), completo=True, qualidade_atingida=True)
        )
        r = _ler(str(tmp_path))
        assert r["qualidade_atingida"] is True

    def test_noop_em_pasta_sem_report(self, pp, tmp_path):
        """Nao deve levantar excecao se o arquivo ainda nao existir."""
        asyncio.run(pp.finalize_report(str(tmp_path), completo=True))


# ===========================================================================
# Concorrencia: simula o asyncio.gather do download_release
# ===========================================================================


class TestConcorrencia:
    """Valida que o lock por pasta previne corrupcao quando varias faixas
    terminam ao mesmo tempo."""

    def test_todas_faixas_chegam_quando_paralelas(self, pp, tmp_path):
        N = 10
        faixas_previstas = [
            {"numero": f"{i + 1:02d}", "id": f"t{i + 1}", "titulo": f"Faixa {i + 1}"}
            for i in range(N)
        ]

        async def cenario():
            await pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Paralelo",
                faixas_previstas=faixas_previstas,
            )
            # Simula N faixas terminando ao mesmo tempo
            await asyncio.gather(
                *[
                    pp.update_track_status(
                        str(tmp_path),
                        numero=f"{i + 1:02d}",
                        item_id=f"t{i + 1}",
                        titulo=f"Faixa {i + 1}",
                        status="ok",
                    )
                    for i in range(N)
                ]
            )
            await pp.finalize_report(str(tmp_path), completo=True)

        asyncio.run(cenario())
        r = _ler(str(tmp_path))
        assert r["resumo"]["total"] == N
        assert r["resumo"]["baixadas"] == N
        assert r["estado"] == "completo"
        # Ordem preservada apesar do gather
        numeros = _numeros(r)
        assert numeros == sorted(numeros, key=lambda x: int(x))

    def test_sem_arquivo_tmp_residual(self, pp, tmp_path):
        async def cenario():
            await asyncio.gather(
                *[
                    pp.update_track_status(
                        str(tmp_path),
                        numero=f"{i:02d}",
                        item_id=f"t{i}",
                        titulo=f"T{i}",
                        status="ok",
                    )
                    for i in range(5)
                ]
            )

        asyncio.run(cenario())
        assert not (tmp_path / ".report.json.tmp").exists()


# ===========================================================================
# Cenario de integracao: faixa solta -> album completo
# (o bug real relatado com o report.json do Benson Boone)
# ===========================================================================


class TestCenarioFaixaSoltaDepoisAlbum:
    """Replica o cenario exato que gerou a duplicata no report.json real:
    Be Someone baixada como faixa avulsa (ID como string na URL) e depois
    o album completo baixado (ID como int no JSON da API)."""

    ID_FAIXA = 257946874  # int, como vem do JSON da API Qobuz
    ID_FAIXA_STR = "257946874"  # str, como viria extraído de uma URL

    def test_sem_duplicata(self, pp, tmp_path):
        # 1. Faixa avulsa baixada (ID como string)
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero=2,
                item_id=self.ID_FAIXA_STR,
                titulo="Be Someone",
                status="ok",
                artista="Benson Boone",
                tipo_default="faixa",
                titulo_default="Fireworks & Rollerblades",
            )
        )

        # 2. Album completo baixado em seguida (IDs como int da API)
        faixas_previstas = [
            {"numero": "01", "id": 257946873, "titulo": "Intro"},
            {"numero": "02", "id": self.ID_FAIXA, "titulo": "Be Someone"},
            {"numero": "03", "id": 257946875, "titulo": "Slow It Down"},
        ]
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Fireworks & Rollerblades",
                artista="Benson Boone",
                item_id="bqexzyruqy65a",
                faixas_previstas=faixas_previstas,
            )
        )
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="01",
                item_id=257946873,
                titulo="Intro",
                status="ok",
            )
        )
        # Be Someone JA estava ok -- nao deve voltar para pendente nem duplicar
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero="03",
                item_id=257946875,
                titulo="Slow It Down",
                status="ok",
            )
        )

        r = _ler(str(tmp_path))
        assert len(r["faixas"]) == 3, (
            f"Duplicata detectada: {len(r['faixas'])} faixas (esperado 3). "
            f"IDs: {_ids(r)}"
        )

    def test_status_ok_preservado_apos_init(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero=2,
                item_id=self.ID_FAIXA_STR,
                titulo="Be Someone",
                status="ok",
                tipo_default="faixa",
                titulo_default="Fireworks & Rollerblades",
            )
        )
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Fireworks & Rollerblades",
                artista="Benson Boone",
                item_id="bqexzyruqy65a",
                faixas_previstas=[
                    {"numero": "01", "id": 257946873, "titulo": "Intro"},
                    {"numero": "02", "id": self.ID_FAIXA, "titulo": "Be Someone"},
                ],
            )
        )
        r = _ler(str(tmp_path))
        # Be Someone deve continuar como ok, nao virar pendente
        status_be = _status(r).get(self.ID_FAIXA_STR) or _status(r).get(
            str(self.ID_FAIXA)
        )
        assert status_be == "ok", (
            f"Status regrediu para: {status_be}. "
            f"Faixas: {[(f['id'], f['status']) for f in r['faixas']]}"
        )

    def test_cabecalho_promovido_para_album(self, pp, tmp_path):
        asyncio.run(
            pp.update_track_status(
                str(tmp_path),
                numero=2,
                item_id=self.ID_FAIXA_STR,
                titulo="Be Someone",
                status="ok",
                tipo_default="faixa",
                titulo_default="Fireworks & Rollerblades",
            )
        )
        asyncio.run(
            pp.init_report(
                str(tmp_path),
                tipo="album",
                titulo="Fireworks & Rollerblades (Deluxe)",
                artista="Benson Boone",
                item_id="bqexzyruqy65a",
            )
        )
        r = _ler(str(tmp_path))
        assert r["tipo"] == "album"
        assert "Deluxe" in r["titulo"]
