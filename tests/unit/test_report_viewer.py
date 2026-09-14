"""Testa qobuz_dl/report_viewer.py -- gera o .html a partir do .report.json.

CONTEXTO
--------
Módulo sem teste dedicado até agora (nenhum arquivo em tests/ menciona
`report_viewer`). Só usa biblioteca padrão (argparse, html, json, os, sys,
webbrowser) -- sem rede, sem prompt_toolkit, sem estado externo além do
arquivo que ele mesmo lê/escreve -- então dá pra cobrir quase tudo com
testes puros de entrada/saída, sem mock pesado.

Cada valor esperado abaixo foi conferido rodando o módulo de verdade
antes de virar assert (inclusive os casos de exceção do _fmt_dt e as 4
saídas de main()), não só lido do código.
"""

import json

import pytest

from qobuz_dl import report_viewer as rv


# --------------------------------------------------------------------
# esc
# --------------------------------------------------------------------
class TestEsc:
    def test_none_vira_string_vazia(self):
        assert rv.esc(None) == ""

    def test_escapa_caracteres_especiais_de_html(self):
        assert rv.esc("a&b<c>") == "a&amp;b&lt;c&gt;"

    def test_numero_e_convertido_pra_string(self):
        assert rv.esc(42) == "42"


# --------------------------------------------------------------------
# _fmt_dt
# --------------------------------------------------------------------
class TestFmtDt:
    def test_none_vira_traco(self):
        assert rv._fmt_dt(None) == "-"

    def test_string_vazia_vira_traco(self):
        assert rv._fmt_dt("") == "-"

    def test_iso_valido_e_formatado(self):
        assert rv._fmt_dt("2026-09-02T07:50:37-03:00") == "02.09.2026 -- 07:50"

    def test_sem_separador_T_cai_no_except_e_devolve_bruto(self):
        # .split("T") devolve 1 item só -> ValueError ao desempacotar
        # data/resto -> cai no except Exception genérico.
        assert rv._fmt_dt("data-invalida-sem-T") == "data-invalida-sem-T"

    def test_data_com_partes_a_mais_ou_a_menos_tambem_cai_no_except(self):
        # "2026-09" só tem 2 partes -- desempacotar em ano/mes/dia (3
        # variáveis) explode, mesmo com o "T" presente e o split inicial
        # ok. Ramo diferente do teste anterior (quebra no 2º split, não
        # no 1º).
        assert rv._fmt_dt("2026-09T07:50:00") == "2026-09T07:50:00"


# --------------------------------------------------------------------
# _badge
# --------------------------------------------------------------------
class TestBadge:
    def test_cor_default_e_chip(self):
        assert rv._badge("Sincronizada") == '<span class="badge chip">Sincronizada</span>'

    def test_cor_customizada(self):
        assert rv._badge("X", "chip-err") == '<span class="badge chip-err">X</span>'


# --------------------------------------------------------------------
# carregar_report
# --------------------------------------------------------------------
class TestCarregarReport:
    def test_caminho_de_arquivo_direto(self, tmp_path):
        arquivo = tmp_path / "custom.json"
        arquivo.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert rv.carregar_report(str(arquivo)) == {"a": 1}

    def test_caminho_de_pasta_usa_report_filename_por_dentro(self, tmp_path):
        (tmp_path / rv.REPORT_FILENAME).write_text(
            json.dumps({"b": 2}), encoding="utf-8"
        )
        assert rv.carregar_report(str(tmp_path)) == {"b": 2}

    def test_arquivo_ausente_propaga_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rv.carregar_report(str(tmp_path / "nao-existe.json"))

    def test_json_corrompido_propaga_json_decode_error(self, tmp_path):
        arquivo = tmp_path / "corrompido.json"
        arquivo.write_text("{nao e json valido", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            rv.carregar_report(str(arquivo))


# --------------------------------------------------------------------
# _renderizar_faixa
# --------------------------------------------------------------------
class TestRenderizarFaixa:
    def test_status_conhecido_usa_label_cor_e_glifo_da_tabela(self):
        out = rv._renderizar_faixa(
            {"numero": 1, "identificacao": {"titulo": "Musica"},
             "download": {"situacao": "concluido"}},
            mostrar_artista_col=False,
        )
        assert 'class="status-pill ok"' in out
        assert "●" in out and "Concluída" in out
        # sem motivo/badges/artista -> nada pra expandir -> linha estática
        assert "<details" not in out
        assert "track-toggle-vazio" in out

    def test_status_desconhecido_cai_no_fallback_generico(self):
        out = rv._renderizar_faixa({"download": {"situacao": "zzz-novo"}}, False)
        assert "zzz-novo" in out
        assert 'class="status-pill muted"' in out and "○" in out

    def test_artista_aparece_so_quando_flag_liga_e_artista_existe(self):
        faixa = {"identificacao": {"artista": "Fulano"}}
        com_flag = rv._renderizar_faixa(faixa, mostrar_artista_col=True)
        sem_flag = rv._renderizar_faixa(faixa, mostrar_artista_col=False)
        assert "<details" in com_flag and 'track-sub">Fulano<' in com_flag
        assert "Fulano" not in sem_flag and "<details" not in sem_flag

    def test_motivo_de_falha_gera_linha_e_expande_a_faixa(self):
        out = rv._renderizar_faixa(
            {"download": {"situacao": "falha", "motivo": "Erro de rede"}}, False
        )
        assert "<details" in out
        assert 'track-motivo">Erro de rede<' in out

    def test_letras_sucesso_sincronizada_bilingue_e_fonte(self):
        out = rv._renderizar_faixa(
            {"letras": {"situacao": "sucesso", "sincronizada": True,
                        "bilingue": True, "fonte": "Genius"}},
            False,
        )
        assert "Sincronizada" in out and "Bilíngue" in out
        assert 'chip-muted">Genius<' in out
        # bilingue=True -> o elif de "sem tradução PT" nem roda
        assert "Sem tradução PT" not in out

    def test_letras_sucesso_sem_bilingue_e_sem_traducao_pt(self):
        out = rv._renderizar_faixa(
            {"letras": {"situacao": "sucesso", "traducao_disponivel": False}}, False
        )
        assert "Sem tradução PT" in out
        assert "Sincronizada" not in out and "Bilíngue" not in out

    def test_letras_falha(self):
        out = rv._renderizar_faixa({"letras": {"situacao": "falha"}}, False)
        assert "Falha ao buscar letra" in out and "chip-err" in out

    def test_letras_nao_encontrada(self):
        out = rv._renderizar_faixa({"letras": {"situacao": "nao_encontrada"}}, False)
        assert "Letra não encontrada" in out

    def test_faixa_totalmente_vazia_usa_todos_os_defaults(self):
        out = rv._renderizar_faixa({}, False)
        assert ">-<" in out  # numero default "-"
        assert "Faixa</span>" in out  # titulo default "Faixa"
        assert "Pendente" in out and "○" in out  # situacao default "pendente"
        assert "<details" not in out


# --------------------------------------------------------------------
# renderizar_html
# --------------------------------------------------------------------
class TestRenderizarHtml:
    def test_relatorio_vazio_usa_todos_os_defaults(self):
        out = rv.renderizar_html({})
        assert "(sem título)" in out
        assert ">Faixa(s)<" in out  # subtitulo cai no _TIPO_LABEL de "faixa"
        assert 'estado-pill muted">Em andamento<' in out
        assert "Nenhuma faixa registrada ainda." in out
        assert out.count('<div class="stat-num">00</div>') == 4
        assert '<span class="pct">0%</span>' in out
        assert 'class="seg on"' not in out  # 0% -> nenhum segmento aceso

    def test_relatorio_completo_calcula_progresso_badges_e_datas(self):
        report = {
            "tipo": "album",
            "identificacao": {
                "titulo": "Título <especial>",
                "artista": "Artista & Cia",
                "tipo_lancamento": "Deluxe",
            },
            "qualidade": {
                "formato": "FLAC", "bit_depth": 24, "sampling_rate": 96,
                "alvo_atingida": True,
            },
            "progresso": {
                "estado": {
                    "situacao": "completo",
                    "criado_em": "2026-01-01T10:00:00-03:00",
                    "atualizado_em": "2026-01-02T11:30:00-03:00",
                },
                "resumo": {
                    "total": 10, "concluidas": 7, "puladas": 1,
                    "falhas": 1, "pendentes": 1,
                },
            },
            "extra": {"rotulo": "Gravadora X", "genero": "Rock", "url": "https://x.com/1"},
            "faixas": [
                {"identificacao": {"titulo": "F1", "artista": "A1"},
                 "download": {"situacao": "concluido"}},
                {"identificacao": {"titulo": "F2", "artista": "A2"},
                 "download": {"situacao": "falha", "motivo": "erro"}},
            ],
        }
        out = rv.renderizar_html(report)
        assert "Título &lt;especial&gt;" in out
        assert "Artista &amp; Cia / Álbum" in out
        assert "Deluxe" in out  # badge tipo_lancamento
        assert "FLAC" in out  # badge formato
        assert "24BIT / 96KHZ" in out  # badge bit_depth+sampling_rate combinado
        assert "Qualidade atingida" in out
        assert 'estado-pill ok">Completo<' in out
        assert '<span class="pct">70%</span>' in out
        assert out.count('class="seg on"') == 14  # round(70/100*20)
        assert "Gravadora X" in out and "Rock" in out
        assert 'href="https://x.com/1"' in out
        assert "02.01.2026 -- 11:30" in out  # atualizado_em formatado
        # 2 faixas com artistas DIFERENTES -> mostrar_artista_col=True
        assert 'track-sub">A1<' in out and 'track-sub">A2<' in out

    def test_alvo_nao_atingido_gera_badge_de_alerta(self):
        out = rv.renderizar_html({"qualidade": {"alvo_atingida": False}})
        assert "Qualidade não atingida" in out

    def test_url_da_identificacao_e_usada_quando_extra_nao_tem_url(self):
        out = rv.renderizar_html({"identificacao": {"url": "https://y.com/2"}})
        assert 'href="https://y.com/2"' in out

    def test_uma_unica_faixa_com_artista_nao_mostra_coluna_de_artista(self):
        # Só quando há MAIS de um artista distinto que a coluna aparece
        # (ver `mostrar_artista_col = len(artistas_distintos) > 1`).
        out = rv.renderizar_html({"faixas": [{"identificacao": {"artista": "Solo"}}]})
        assert "Solo" not in out


# --------------------------------------------------------------------
# main
# --------------------------------------------------------------------
class TestMain:
    def _rodar(self, monkeypatch, capsys, argv):
        monkeypatch.setattr("sys.argv", ["report_viewer.py", *argv])
        codigo = 0
        try:
            rv.main()
        except SystemExit as e:
            codigo = e.code
        saida = capsys.readouterr()
        return codigo, saida.out, saida.err

    def _report_de_exemplo(self, tmp_path):
        arquivo = tmp_path / rv.REPORT_FILENAME
        arquivo.write_text(
            json.dumps({"identificacao": {"titulo": "T"}}), encoding="utf-8"
        )
        return arquivo

    def test_sucesso_com_caminho_de_arquivo_gera_html_ao_lado(
        self, tmp_path, monkeypatch, capsys
    ):
        arquivo = self._report_de_exemplo(tmp_path)
        codigo, out, err = self._rodar(monkeypatch, capsys, [str(arquivo)])
        saida_esperada = tmp_path / "report.html"
        assert codigo == 0
        assert saida_esperada.exists()
        assert f"Gerado: {saida_esperada}" in out

    def test_sucesso_com_caminho_de_pasta_usa_a_propria_pasta_como_base(
        self, tmp_path, monkeypatch, capsys
    ):
        self._report_de_exemplo(tmp_path)
        codigo, out, err = self._rodar(monkeypatch, capsys, [str(tmp_path)])
        assert codigo == 0
        assert (tmp_path / "report.html").exists()

    def test_flag_o_customiza_o_arquivo_de_saida(self, tmp_path, monkeypatch, capsys):
        arquivo = self._report_de_exemplo(tmp_path)
        saida_custom = tmp_path / "saida-custom.html"
        codigo, out, err = self._rodar(
            monkeypatch, capsys, [str(arquivo), "-o", str(saida_custom)]
        )
        assert codigo == 0
        assert saida_custom.exists()

    def test_arquivo_ausente_sai_com_codigo_1_e_avisa_no_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        codigo, out, err = self._rodar(
            monkeypatch, capsys, [str(tmp_path / "nao-existe.json")]
        )
        assert codigo == 1
        assert "Nao encontrei" in err

    def test_json_corrompido_sai_com_codigo_1_e_avisa_no_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        arquivo = tmp_path / rv.REPORT_FILENAME
        arquivo.write_text("{invalido", encoding="utf-8")
        codigo, out, err = self._rodar(monkeypatch, capsys, [str(arquivo)])
        assert codigo == 1
        assert "invalido/corrompido" in err

    def test_flag_abrir_chama_webbrowser_open_com_file_url(
        self, tmp_path, monkeypatch, capsys
    ):
        arquivo = self._report_de_exemplo(tmp_path)
        chamadas = []
        monkeypatch.setattr(rv.webbrowser, "open", chamadas.append)
        codigo, out, err = self._rodar(monkeypatch, capsys, [str(arquivo), "--abrir"])
        assert codigo == 0
        assert len(chamadas) == 1
        assert chamadas[0].startswith("file://")
