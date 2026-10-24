"""Testes da tabela com checkbox (lógica e desenho; sem terminal)."""

from qobuz_dl import ui
from qobuz_dl.artist_checklist import (
    ChecklistState,
    footer_fragments,
    footer_height,
    detail_fragments,
    header_fragments,
    header_height,
    list_fragments,
    profile_url,
    release_lines,
    table_layout,
)


def _artists(*names, reviewed=(), pending=None):
    pending = pending or {}
    out = []
    for i, name in enumerate(names, 1):
        out.append(
            {
                "source_artist_id": str(i),
                "name": name,
                "last_reviewed_at": "2026-10-01T10:00:00" if name in reviewed else None,
                "pending_count": pending.get(name, 0),
            }
        )
    return out


def _linhas(fragmentos):
    """Junta os fragmentos e separa em linhas de texto puro."""
    return "".join(f[1] for f in fragmentos).split("\n")


def _largura(texto):
    return ui._cell_width(texto)


# -- lógica -------------------------------------------------------------------


def test_marca_e_desmarca_artista():
    state = ChecklistState(_artists("Ana", "Bia"))
    assert state.toggle(1) is True
    assert state.selected_ids() == ["2"]
    assert state.toggle() is True  # cursor ficou na Bia
    assert state.selected_ids() == []


def test_conferido_aparece_marcado_e_nao_entra_na_confirmacao():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    row = state.rows[0]
    assert row.verified is True
    assert state.check_glyph(row) == "✔"
    assert state.status_of(row) == ("CONFERIDO", "done")
    assert state.selected_ids() == []


def test_espaco_em_conferido_marca_para_desfazer_e_desmarca():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    row = state.rows[0]
    assert state.toggle(0) is True
    assert state.undo_ids() == ["1"] and state.selected_ids() == []
    assert state.status_of(row) == ("A DESFAZER", "undo")
    assert state.check_glyph(row) == "↺"
    assert "a playlist não é apagada" in state.message
    assert state.toggle(0) is True
    assert state.undo_ids() == []


def test_conferido_com_lancamento_novo_volta_a_ser_pendente():
    state = ChecklistState(_artists("Ana", reviewed=("Ana",), pending={"Ana": 2}))
    row = state.rows[0]
    assert row.verified is False
    assert state.status_of(row) == ("COM NOVOS", "new")
    assert state.toggle(0) is True
    assert state.status_of(row) == ("A CONFIRMAR", "picked")
    assert state.check_glyph(row) == "*"


def test_busca_ignora_acentos_e_caixa():
    state = ChecklistState(_artists("Beyoncé", "Bia", "Sigur Rós"))
    state.start_search()
    for ch in "BEYONCE":
        state.type_char(ch)
    assert [r.name for r in state.visible()] == ["Beyoncé"]
    state.backspace()
    state.clear_filters()
    assert len(state.visible()) == 3


def test_filtro_so_pendentes_aparece_no_rodape():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    state.toggle_only_pending()
    assert [r.name for r in state.visible()] == ["Bia"]
    textos = " ".join(t for _s, t in state.footer_lines(80))
    assert "só pendentes" in textos and "Conferidos: 1/2" in textos


def test_toggle_usa_indice_da_lista_filtrada():
    state = ChecklistState(_artists("Ana", "Bia", "Caio"))
    state.query = "caio"
    assert state.toggle(0) is True
    assert state.selected_ids() == ["3"]


def test_sair_com_marcados_pede_segundo_q():
    state = ChecklistState(_artists("Ana"))
    state.toggle(0)
    assert state.request_quit() is False
    assert "q de novo descarta" in state.message
    assert state.request_quit() is True


def test_sair_sem_marcados_e_imediato():
    assert ChecklistState(_artists("Ana")).request_quit() is True


def test_recarregar_mantem_cursor_no_mesmo_artista_e_solta_os_confirmados():
    state = ChecklistState(_artists("Ana", "Bia", "Caio"))
    state.toggle(0)
    state.toggle(2)
    state.cursor = 2
    state.load(_artists("Ana", "Bia", "Caio", reviewed=("Ana",)), keep_selected={"3"})
    assert state.current().name == "Caio"
    assert state.selected_ids() == ["3"]


def test_navegacao_respeita_os_limites():
    state = ChecklistState(_artists("Ana", "Bia"))
    state.move(-5)
    assert state.cursor == 0
    state.move(10)
    assert state.cursor == 1
    state.first()
    assert state.cursor == 0
    state.last()
    assert state.cursor == 1


def test_lista_vazia_nao_quebra():
    state = ChecklistState([])
    assert state.current() is None
    assert state.toggle() is False
    state.move(1)
    view = {}
    texto = "".join(f[1] for f in list_fragments(state, 100, 20, view))
    assert "Nenhum artista seguido" in texto


def test_url_do_perfil():
    assert profile_url(42) == "https://open.qobuz.com/artist/42"


# -- tabela e cartões ----------------------------------------------------------


def test_tabela_so_em_tela_larga_e_cartao_em_tela_estreita():
    assert table_layout(100)[0] is True
    assert table_layout(45)[0] is False
    assert table_layout(70, with_open=False)[0] is True
    assert table_layout(70, with_open=True)[0] is False


def test_cabecalho_da_tabela_tem_bordas_e_colunas():
    state = ChecklistState(_artists("Ana"))
    linhas = _linhas(header_fragments(state, 100))
    assert linhas[0].strip() == "=== ARTISTAS SEGUIDOS ==="
    assert linhas[1].strip().startswith("┌") and linhas[1].strip().endswith("┐")
    for nome in ("ARTISTA", "NOVOS", "STATUS", "CONFERIDO EM", "PERFIL"):
        assert nome in linhas[2]
    assert linhas[3].strip().startswith("├")
    assert len(linhas) == header_height(100) == 4


def test_cabecalho_em_tela_estreita_so_tem_o_titulo():
    state = ChecklistState(_artists("Ana"))
    assert len(_linhas(header_fragments(state, 45))) == header_height(45) == 1


def test_linhas_da_tabela_cabem_na_tela_e_alinham_as_bordas():
    nomes = ["Ana", "A" * 60, "宇多田ヒカル", "Sigur Rós"]
    state = ChecklistState(_artists(*nomes, pending={"Ana": 3}))
    state.toggle(1)
    for columns in (80, 100, 140):
        linhas = _linhas(header_fragments(state, columns)) + _linhas(
            list_fragments(state, columns, 40, {})
        )
        tabela = [l.rstrip() for l in linhas if any(c in l for c in "┌├└│")]
        assert all(_largura(l) <= columns for l in linhas), columns
        # cada linha de tabela termina na mesma coluna que a borda
        assert len({_largura(l) for l in tabela}) == 1, columns


def test_linha_atual_usa_ponteiro_e_fundo_de_destaque():
    state = ChecklistState(_artists("Ana", "Bia"))
    fragmentos = list_fragments(state, 100, 20, {})
    primeira = [f for f in fragmentos if f[1].strip() == "➤"]
    assert primeira and primeira[0][0] == "class:hovered"
    depois = fragmentos[fragmentos.index(primeira[0]) + 1 :]
    assert "➤" not in "".join(f[1] for f in depois)


def test_cores_por_estado_na_tabela():
    state = ChecklistState(
        _artists("Ana", "Bia", "Caio", reviewed=("Ana",), pending={"Bia": 2})
    )
    state.cursor = 2  # Caio é a linha atual; Ana e Bia mostram as cores
    estilos = {}
    for estilo, texto, *_ in list_fragments(state, 120, 40, {}):
        estilos.setdefault(texto.strip(), set()).add(estilo)
    assert estilos["CONFERIDO"] == {"class:done"}
    assert estilos["COM NOVOS"] == {"class:new"}
    assert "class:open" in estilos["[abrir]"]
    assert "class:hovered" in estilos["PENDENTE"]  # linha atual: fundo de destaque


def test_fatia_de_linhas_acompanha_o_cursor():
    state = ChecklistState(_artists(*[f"Artista {i:02d}" for i in range(30)]))
    view = {}
    list_fragments(state, 100, 10, view)  # 5 itens por tela (2 linhas cada)
    assert view["per_page"] == 5 and view["top"] == 0
    state.cursor = 20
    texto = "".join(f[1] for f in list_fragments(state, 100, 10, view))
    assert "Artista 20" in texto and "Artista 00" not in texto
    assert texto.count("\n") + 1 <= 10  # nunca passa da altura disponível
    assert texto.rstrip().splitlines()[-1].strip().startswith("└")  # fecha a tabela


def test_cartoes_em_tela_estreita_cabem_e_mostram_estado():
    nomes = ["Ana", "B" * 50, "宇多田ヒカル"]
    state = ChecklistState(
        _artists(*nomes, reviewed=("Ana",), pending={"宇多田ヒカル": 2})
    )
    state.toggle(1)
    for columns in (36, 45, 60):
        fragmentos = list_fragments(state, columns, 40, {})
        linhas = _linhas(fragmentos)
        assert all(_largura(l) <= columns for l in linhas), columns
        texto = "\n".join(linhas)
        assert "╭" in texto and "┏" in texto  # marcado ganha borda grossa
        assert "CONFERIDO" in texto and "A CONFIRMAR" in texto
        assert "[abrir]" in texto and "👤" in texto


def test_tocar_na_linha_e_em_abrir_usam_tratadores_diferentes():
    state = ChecklistState(_artists("Ana"))
    chamados = []

    def handlers(tipo, indice):
        chamados.append((tipo, indice))
        return lambda event: None

    fragmentos = list_fragments(state, 100, 20, {}, handlers)
    com_tratador = [f for f in fragmentos if len(f) == 3]
    assert com_tratador
    assert {("open", 0), ("toggle", 0), ("detail", 0)} <= set(chamados)
    abrir = [f for f in com_tratador if f[1].strip() == "[abrir]"]
    assert len(abrir) == 1


def test_rodape_tem_altura_constante_e_cabe_na_tela():
    state = ChecklistState(_artists("Ana"))
    alturas = {footer_height(state, 100)}
    state.message = "Perfil aberto: Ana."
    alturas.add(footer_height(state, 100))
    assert len(alturas) == 1
    for columns in (36, 45, 100):
        for _estilo, texto in state.footer_lines(columns):
            assert _largura(texto) <= columns
    texto = "".join(f[1] for f in footer_fragments(state, 100))
    assert "Perfil aberto: Ana." in texto and "Espaço marca" in texto


def test_tabela_de_lancamentos_do_sorteio():
    lancamentos = [
        {"title": "Disco Novo", "release_type": "album", "release_date": "2099-01-01"},
        {"title": "Single " * 20, "release_type": None, "release_date": None},
    ]
    for columns in (45, 80, 120):
        linhas = release_lines(lancamentos, columns)
        assert all(_largura(l) <= columns - 1 for l in linhas), columns
    larga = "\n".join(release_lines(lancamentos, 100))
    assert "LANÇAMENTO" in larga and "TIPO" in larga and "DATA" in larga
    assert "Disco Novo" in larga and "┌" in larga
    assert "Nenhum lançamento" in release_lines([], 80)[0]


# -- desfazer, marcar todos, ordem, restauração ---------------------------------


def test_desfazer_pede_segundo_enter_e_espaco_cancela():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    assert state.request_confirm() is False
    assert "Nada marcado" in state.message
    state.toggle(0)  # Ana: desfazer
    assert state.request_confirm() is False
    assert "Desfazer 1 conferência" in state.message
    assert state.request_confirm() is True  # segundo Enter confirma
    state.undo_armed = False
    state.toggle(0)  # cancela o desfazer
    assert state.request_confirm() is False
    assert "Nada marcado" in state.message


def test_enter_com_pendentes_marcados_confirma_direto():
    state = ChecklistState(_artists("Ana", "Bia"))
    state.toggle(1)
    assert state.request_confirm() is True


def test_sair_conta_marcados_a_confirmar_e_a_desfazer():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    state.toggle(0)
    assert state.request_quit() is False
    assert "1 marcado(s) sem confirmar" in state.message
    assert state.request_quit() is True


def test_marcar_todos_respeita_busca_e_alterna():
    state = ChecklistState(_artists("Ana", "Ana Júlia", "Bia", reviewed=("Ana",)))
    state.query = "ana"
    assert state.select_all_visible() == 1  # só "Ana Júlia": Ana já está conferida
    assert state.selected_ids() == ["2"]
    assert state.select_all_visible() == 1  # já estavam todos: desmarca
    assert state.selected_ids() == []
    state.clear_filters()
    state.select_all_visible()
    assert state.selected_ids() == ["2", "3"]


def test_marcar_todos_sem_pendentes_avisa():
    state = ChecklistState(_artists("Ana", reviewed=("Ana",)))
    assert state.select_all_visible() == 0
    assert "Nenhum pendente" in state.message


def test_ordem_novos_primeiro_e_cursor_acompanha():
    state = ChecklistState(_artists("Ana", "Bia", "Caio", pending={"Caio": 2}))
    state.cursor = 1  # Bia
    state.toggle_sort()
    assert [r.name for r in state.visible()] == ["Caio", "Ana", "Bia"]
    assert state.current().name == "Bia"
    state.toggle_sort()
    assert [r.name for r in state.visible()] == ["Ana", "Bia", "Caio"]
    assert state.current().name == "Bia"


def test_restaurar_marcacoes_ignora_conferidos_e_desconhecidos():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    assert state.restore_selected(["1", "2", "99"]) == 1
    assert state.selected_ids() == ["2"]


def test_on_change_avisa_a_cada_marcacao():
    state = ChecklistState(_artists("Ana", "Bia"))
    vistos = []
    state.on_change = lambda s: vistos.append(s.selected_ids())
    state.toggle(0)
    state.toggle(1)
    state.select_all_visible()
    assert vistos == [["1"], ["1", "2"], []]


def test_recarregar_limpa_desfazer_exceto_falhas():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana", "Bia")))
    state.toggle(0)
    state.toggle(1)
    state.load(_artists("Ana", "Bia", reviewed=("Bia",)), keep_undo={"2"})
    assert state.undo_ids() == ["2"]


# -- tela do artista ------------------------------------------------------------

_LANCAMENTOS = [
    {"title": f"Disco {i}", "release_type": "album", "release_date": f"2026-10-{i:02d}"}
    for i in range(1, 16)
]


def _detalhe(columns=100, avail=30, **kw):
    state = ChecklistState(
        _artists("Ana", "Bia", pending={"Bia": 15}), releases={"2": _LANCAMENTOS}
    )
    state.cursor = 1
    assert state.open_detail() is True
    for chave, valor in kw.items():
        setattr(state, chave, valor)
    return state, "".join(f[1] for f in detail_fragments(state, columns, avail))


def test_detalhe_mostra_artista_perfil_botoes_e_tabela():
    state, texto = _detalhe()
    assert "Bia" in texto and "COM NOVOS" in texto and "15 novo(s)" in texto
    assert "open.qobuz.com/artist/2" in texto
    assert "[abrir]" in texto and "[marcar]" in texto and "[voltar]" in texto
    assert "LANÇAMENTO" in texto and "┌" in texto and "└" in texto
    assert header_height(100, state) == 1
    assert state.title() == "=== Bia ==="


def test_detalhe_cabe_na_altura_e_rola():
    state, texto = _detalhe(avail=14)
    assert texto.count("\n") + 1 <= 14
    assert "mostrando 1-" in texto and "de 15" in texto
    primeira = texto
    state.scroll_detail(50)  # rola além do fim: a tela corrige
    texto2 = "".join(f[1] for f in detail_fragments(state, 100, 14))
    assert texto2 != primeira and "mostrando 1-" not in texto2
    assert "Disco 15" in texto2 and "-15 de 15" in texto2


def test_detalhe_em_tela_estreita_vira_lista_compacta():
    state, texto = _detalhe(columns=45, avail=30)
    assert "┌" not in texto and "•" in texto and "Disco 1" in texto
    assert all(_largura(l) <= 45 for l in texto.split("\n"))


def test_detalhe_sem_lancamentos_pendentes():
    state = ChecklistState(_artists("Ana"))
    state.open_detail()
    texto = "".join(f[1] for f in detail_fragments(state, 100, 20))
    assert "Nenhum lançamento pendente registrado." in texto


def test_detalhe_botoes_usam_tratadores_e_marcar_alterna_o_rotulo():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    state.open_detail()
    chamados = []

    def handlers(tipo, indice):
        chamados.append(tipo)
        return lambda event: None

    detail_fragments(state, 100, 20, handlers)
    assert {"detail_open", "detail_toggle", "detail_back"} <= set(chamados)
    assert "[desfazer]" in "".join(f[1] for f in detail_fragments(state, 100, 20))
    state.toggle_detail()
    assert "[cancelar]" in "".join(f[1] for f in detail_fragments(state, 100, 20))


def test_detalhe_marca_o_artista_e_volta_para_a_lista():
    state = ChecklistState(_artists("Ana", "Bia"))
    state.cursor = 1
    state.open_detail()
    assert state.toggle_detail() is True
    assert state.selected_ids() == ["2"]
    state.close_detail()
    assert state.view == "list"
    footer = " ".join(t for _s, t in state.footer_lines(120))
    assert "D detalhes" in footer


def test_rodape_do_detalhe_tem_as_teclas_da_tela():
    state = ChecklistState(_artists("Ana"))
    state.open_detail()
    footer = " ".join(t for _s, t in state.footer_lines(120))
    assert "O abre o perfil" in footer and "Esc volta" in footer


def test_recarregar_fecha_detalhe_de_artista_que_sumiu():
    state = ChecklistState(_artists("Ana", "Bia"))
    state.cursor = 1
    state.open_detail()
    state.load(_artists("Ana"))
    assert state.view == "list"


def test_cores_e_status_a_desfazer_na_tabela():
    state = ChecklistState(_artists("Ana", "Bia", reviewed=("Ana",)))
    state.cursor = 1
    state.toggle(0)
    state.cursor = 1
    estilos = {}
    for estilo, texto, *_ in list_fragments(state, 120, 40, {}):
        estilos.setdefault(texto.strip(), set()).add(estilo)
    assert estilos["A DESFAZER"] == {"class:undo"}
