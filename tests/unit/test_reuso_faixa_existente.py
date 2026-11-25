"""Reaproveitar a faixa já baixada em outra pasta: SÓ depois de conferir tudo.

Regressão: baixar o álbum DEPOIS de ter baixado faixas dele por uma playlist
baixava tudo de novo. Agora o arquivo existente é copiado, mas apenas se o ID,
a duração, a qualidade real e a integridade conferirem com o Qobuz.
"""

import asyncio
import inspect
import os

import pytest

from qobuz_dl import downloader, reuso
from qobuz_dl.db import create_db, handle_download_id

pytestmark = pytest.mark.unit

FLAC_2496 = reuso.InfoAudio("flac", 24, 96.0, 252.0, 0)
FLAC_1644 = reuso.InfoAudio("flac", 16, 44.1, 252.0, 0)
MP3_320 = reuso.InfoAudio("mp3", 0, 44.1, 252.0, 320000)
QOBUZ_2496 = {
    "track_id": 1,
    "format_id": 27,
    "bit_depth": 24,
    "sampling_rate": 96,
    "duration": 252,
}


@pytest.fixture
def banco(tmp_path):
    return create_db(str(tmp_path / "downloads.db"))


@pytest.fixture
def arquivos(monkeypatch):
    """Áudio de mentira (só bytes): a leitura de tags/propriedades é simulada."""
    tabela = {}  # caminho -> (InfoAudio | None, id_da_tag, isrc_da_tag)
    monkeypatch.setattr(reuso, "_info_do_arquivo", lambda c: tabela[c][0])
    monkeypatch.setattr(reuso, "_id_gravado_no_arquivo", lambda c: tabela[c][1])
    monkeypatch.setattr(reuso, "_isrc_do_arquivo", lambda c: tabela[c][2])
    monkeypatch.setattr(reuso, "_limpar_tags", lambda c, mp3: None)
    return tabela


def _faixa(
    tmp_path, arquivos, nome, info, tid="1", isrc="USRC17607839", dados=b"AUDIO"
):
    caminho = tmp_path / nome
    caminho.write_bytes(dados)
    arquivos[str(caminho)] = (info, tid, isrc)
    return str(caminho)


async def _registrar(banco, track_id, caminho, quality=27):
    await handle_download_id(
        banco,
        track_id,
        add_id=True,
        media_type="track",
        quality=quality,
        saved_path=str(caminho),
    )


def _verificador(resultado=(True, "")):
    chamadas = []

    async def verificar(caminho):
        chamadas.append(caminho)
        return resultado

    verificar.chamadas = chamadas
    return verificar


def _esperado(**extra):
    return reuso.esperado_do_qobuz({**QOBUZ_2496, **extra}, {"isrc": "US-RC1-76-07839"})


def test_esperado_do_qobuz_normaliza_os_campos():
    e = reuso.esperado_do_qobuz(
        {
            "track_id": "9",
            "format_id": "6",
            "bits_depth": 16,
            "sampling_rate": 44100,
            "duration": "200.5",
        },
        {"isrc": "ab-cde-12-34567"},
    )
    assert (e.track_id, e.format_id, e.bit_depth, e.taxa_khz, e.duracao, e.isrc) == (
        "9",
        6,
        16,
        44.1,
        200.5,
        "ABCDE1234567",
    )
    assert reuso.esperado_do_qobuz(None, None) == reuso.Esperado()


@pytest.mark.parametrize(
    ("info", "isrc", "esperado", "trecho"),
    [
        (FLAC_2496, "USRC17607839", _esperado(), ""),
        (FLAC_1644, "USRC17607839", _esperado(), "qualidade menor"),
        # cópia MELHOR que a pedida é aceita (nunca rebaixada nem baixada de novo)
        (reuso.InfoAudio("flac", 24, 192.0, 252.0, 0), "USRC17607839", _esperado(), ""),
        (
            FLAC_2496,
            "USRC17607839",
            _esperado(format_id=6, bit_depth=16, sampling_rate=44.1),
            "",
        ),
        (
            reuso.InfoAudio("flac", 16, 96.0, 252.0, 0),
            "USRC17607839",
            _esperado(),
            "qualidade menor",
        ),
        (
            reuso.InfoAudio("flac", 24, 96.0, 200.0, 0),
            "USRC17607839",
            _esperado(),
            "duração diferente",
        ),
        (FLAC_2496, "GBAAA0000001", _esperado(), "ISRC"),
        (FLAC_2496, "", _esperado(), ""),  # sem ISRC na tag: não reprova
        (FLAC_2496, "USRC17607839", _esperado(track_id=2), "outro ID"),
        (MP3_320, "USRC17607839", _esperado(), "entrega FLAC"),
        (FLAC_2496, "USRC17607839", _esperado(format_id=5), "entrega MP3"),
        (MP3_320, "USRC17607839", _esperado(format_id=5), ""),
        (
            reuso.InfoAudio("mp3", 0, 44.1, 252.0, 128000),
            "USRC17607839",
            _esperado(format_id=5),
            "bitrate",
        ),
        (FLAC_2496, "USRC17607839", _esperado(format_id=None), "formato"),
    ],
)
def test_conferir_com_qobuz(info, isrc, esperado, trecho):
    motivo = reuso.conferir_com_qobuz(info, isrc, esperado, "1")
    assert (trecho in motivo) if trecho else motivo == ""


async def test_copia_quando_tudo_confere(tmp_path, banco, arquivos):
    origem = _faixa(
        tmp_path, arquivos, "Artista - Faixa.flac", FLAC_2496, dados=b"FLACDATA"
    )
    await _registrar(banco, "1", origem)
    destino = str(tmp_path / "~tmp_01.tmp")
    verificar = _verificador()

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, destino, _esperado(), verificar
    )

    assert r.formato == 27 and r.origem == origem
    assert "integridade OK" in r.detalhes and "FLAC 24-bit/96 kHz" in r.detalhes
    assert open(destino, "rb").read() == b"FLACDATA"
    assert open(origem, "rb").read() == b"FLACDATA"  # a cópia da playlist fica
    assert verificar.chamadas == [origem]


@pytest.mark.parametrize(
    ("resultado", "trecho"),
    [
        ((False, "invalid data"), "corrompido"),
        ((None, "ffmpeg ausente"), "não verificável"),
    ],
)
async def test_integridade_ruim_ou_impossivel_nao_reaproveita(
    tmp_path, banco, arquivos, resultado, trecho
):
    origem = _faixa(tmp_path, arquivos, "f.flac", FLAC_2496)
    await _registrar(banco, "1", origem)
    destino = str(tmp_path / "~tmp.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, destino, _esperado(), _verificador(resultado)
    )

    assert r.formato is None and trecho in r.motivo
    assert not os.path.exists(destino)


async def test_integridade_que_estoura_o_tempo_nao_reaproveita(
    tmp_path, banco, arquivos, monkeypatch
):
    origem = _faixa(tmp_path, arquivos, "f.flac", FLAC_2496)
    await _registrar(banco, "1", origem)
    monkeypatch.setattr(reuso, "TEMPO_INTEGRIDADE_S", 0.05)

    async def lento(caminho):
        await asyncio.sleep(1)
        return True, ""

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, str(tmp_path / "t.tmp"), _esperado(), lento
    )

    assert r.formato is None and "tempo limite" in r.motivo


async def test_qualidade_menor_recusa_sem_rodar_o_ffmpeg(tmp_path, banco, arquivos):
    origem = _faixa(tmp_path, arquivos, "cd.flac", FLAC_1644)
    await _registrar(banco, "1", origem)
    verificar = _verificador()

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, str(tmp_path / "t.tmp"), _esperado(), verificar
    )

    assert r.formato is None and "qualidade menor" in r.motivo
    assert verificar.chamadas == []  # a conferência barata vem antes da cara


async def test_tag_com_id_de_outra_faixa_nao_reaproveita(tmp_path, banco, arquivos):
    origem = _faixa(tmp_path, arquivos, "f.flac", FLAC_2496, tid="999")
    await _registrar(banco, "1", origem)

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, str(tmp_path / "t.tmp"), _esperado(), _verificador()
    )

    assert r.formato is None and "ID gravado" in r.motivo


async def test_copia_gravada_em_outra_qualidade_serve_se_a_real_confere(
    tmp_path, banco, arquivos
):
    origem = _faixa(tmp_path, arquivos, "cd.flac", FLAC_1644, dados=b"CD")
    await _registrar(banco, "1", origem, quality=6)
    esperado = reuso.esperado_do_qobuz(
        {"format_id": 6, "bit_depth": 16, "sampling_rate": 44.1, "duration": 252}, {}
    )
    destino = str(tmp_path / "t.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, destino, esperado, _verificador()
    )

    assert r.formato == 6 and open(destino, "rb").read() == b"CD"


async def test_copia_de_qualidade_maior_que_a_pedida_e_mantida(
    tmp_path, banco, arquivos
):
    """Tem a faixa em 24/96 e o pedido agora é CD: copia a melhor, não baixa."""
    origem = _faixa(tmp_path, arquivos, "hires.flac", FLAC_2496, dados=b"HIRES")
    await _registrar(banco, "1", origem, quality=27)
    esperado = reuso.esperado_do_qobuz(
        {"format_id": 6, "bit_depth": 16, "sampling_rate": 44.1, "duration": 252}, {}
    )
    destino = str(tmp_path / "t.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 6, destino, esperado, _verificador()
    )

    assert r.formato == 6 and r.origem == origem
    assert "qualidade maior que a pedida" in r.detalhes
    assert open(destino, "rb").read() == b"HIRES"


async def test_copia_de_qualidade_igual_nao_diz_que_e_maior(tmp_path, banco, arquivos):
    origem = _faixa(tmp_path, arquivos, "f.flac", FLAC_2496)
    await _registrar(banco, "1", origem)

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, str(tmp_path / "t.tmp"), _esperado(), _verificador()
    )

    assert r.formato == 27 and "maior" not in r.detalhes


async def test_arquivo_movido_e_achado_pela_tag_e_reaproveitado(
    tmp_path, banco, arquivos, monkeypatch
):
    """O banco aponta para um caminho que não existe mais; o arquivo está em
    outra pasta da biblioteca (achado pela tag) e o banco é corrigido."""
    from qobuz_dl import faixa_existente as fe
    from qobuz_dl import playlist_links as pl

    fe.esquecer_indice()
    biblioteca = tmp_path / "lib"
    (biblioteca / "Outra pasta").mkdir(parents=True)
    novo = _faixa(
        biblioteca, arquivos, "Outra pasta/f.flac", FLAC_2496, dados=b"MOVIDO"
    )
    await _registrar(banco, "1", str(biblioteca / "Album" / "f.flac"))
    monkeypatch.setattr(fe, "_ler_id", lambda c: arquivos[c][1])
    monkeypatch.setattr(pl, "read_track_id", lambda c: arquivos[c][1])
    monkeypatch.setattr(fe, "_ler_info", lambda c: ("flac", 24, 96.0))
    destino = str(tmp_path / "t.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco,
        "1",
        27,
        destino,
        _esperado(),
        _verificador(),
        raiz_biblioteca=str(biblioteca),
    )

    fe.esquecer_indice()
    assert r.formato == 27 and r.origem == novo
    assert open(destino, "rb").read() == b"MOVIDO"
    assert await handle_download_id(banco, "1", add_id=False, quality=27) is not None


async def test_faixa_so_no_disco_sem_registro_e_reaproveitada_pelo_indice(
    tmp_path, banco, arquivos, monkeypatch
):
    """O banco não conhece a faixa, mas ela está na biblioteca (tag com o ID):
    é achada pelo índice e, conferida com o Qobuz, copiada em vez de baixada."""
    from qobuz_dl import faixa_existente as fe

    fe.esquecer_indice()
    biblioteca = tmp_path / "lib"
    (biblioteca / "Playlist" / "Minha").mkdir(parents=True)
    # faixa só em CD (nível 6) e o pedido é 24-bit: o Qobuz também só tem CD
    origem = _faixa(
        biblioteca, arquivos, "Playlist/Minha/f.flac", FLAC_1644, dados=b"CDONLY"
    )
    monkeypatch.setattr(fe, "_ler_id", lambda c: arquivos[c][1])
    monkeypatch.setattr(fe, "_ler_info", lambda c: ("flac", 16, 44.1))
    esperado = reuso.esperado_do_qobuz(
        {"format_id": 27, "bit_depth": 16, "sampling_rate": 44.1, "duration": 252}, {}
    )
    destino = str(tmp_path / "t.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco,
        "1",
        27,
        destino,
        esperado,
        _verificador(),
        raiz_biblioteca=str(biblioteca),
    )

    fe.esquecer_indice()
    assert r.formato == 27 and r.origem == origem
    assert open(destino, "rb").read() == b"CDONLY"


async def test_candidatas_ignora_registro_de_arquivo_sumido_sem_apagar(
    tmp_path, banco, arquivos, caplog
):
    """Consultar o banco para reaproveitar é só leitura: registro com arquivo
    sumido não vira candidata, não é apagado e não gera aviso."""
    import logging

    from qobuz_dl.faixa_existente import linhas_da_faixa

    sumiu = str(tmp_path / "sumiu" / "f.flac")
    await _registrar(banco, "1", sumiu)

    with caplog.at_level(logging.DEBUG):
        achadas = await reuso._candidatas(banco, "1", 27)
        de_novo = await reuso._candidatas(banco, "1", 27)

    assert achadas == [] and de_novo == []
    assert [r for r in caplog.records if "obsoleto" in r.getMessage()] == []
    # o registro continua lá (quem limpa é o download normal, com a faixa refeita)
    assert [x["saved_path"] for x in linhas_da_faixa(banco, "1")] == [sumiu]


async def test_arquivo_movido_sem_raiz_da_biblioteca_baixa_normal(
    tmp_path, banco, arquivos
):
    await _registrar(banco, "1", str(tmp_path / "sumiu" / "f.flac"))

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, str(tmp_path / "t.tmp"), _esperado(), _verificador()
    )

    assert r == reuso.Reaproveitamento()


@pytest.mark.parametrize(
    ("usa_banco", "track_id"), [(False, "1"), (True, ""), (True, "777")]
)
async def test_sem_banco_sem_id_ou_sem_registro_nao_ha_o_que_avisar(
    tmp_path, banco, usa_banco, track_id
):
    r = await reuso.adotar_copia_com_origem(
        banco if usa_banco else None,
        track_id,
        27,
        str(tmp_path / "t"),
        _esperado(),
        _verificador(),
    )
    assert r == reuso.Reaproveitamento()


async def test_falha_ao_copiar_nao_deixa_arquivo_pela_metade(
    tmp_path, banco, arquivos, monkeypatch
):
    origem = _faixa(tmp_path, arquivos, "f.flac", FLAC_2496)
    await _registrar(banco, "1", origem)

    def _explode(caminho, mp3):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(reuso, "_limpar_tags", _explode)
    destino = str(tmp_path / "~tmp.tmp")

    r = await reuso.adotar_copia_com_origem(
        banco, "1", 27, destino, _esperado(), _verificador()
    )

    assert r.formato is None and "copiar" in r.motivo
    assert not os.path.exists(destino) and not os.path.exists(destino + ".reuso")
    assert os.path.exists(origem)


def test_pasta_de_origem_relativa_a_biblioteca(tmp_path):
    base = tmp_path / "lib"
    arq = base / "Playlist" / "Minha playlist" / "Artista - Faixa.flac"
    assert reuso.pasta_de_origem(str(arq), str(base)) == "Playlist/Minha playlist"
    assert (
        reuso.pasta_de_origem(str(base / "x.flac"), str(base)) == "(pasta de downloads)"
    )
    fora = tmp_path / "fora" / "x.flac"
    assert reuso.pasta_de_origem(str(fora), str(base)) == str(tmp_path / "fora")
    assert reuso.pasta_de_origem(str(fora), None) == str(tmp_path / "fora")


def test_pipeline_do_album_confere_antes_de_reaproveitar():
    """Trava de regressão: a conferência com o Qobuz e a integridade não podem sumir."""
    fonte = inspect.getsource(downloader.Download._download_and_tag_impl)
    assert "adotar_copia_com_origem(" in fonte
    assert "esperado_do_qobuz(track_url_dict, track_metadata)" in fonte
    assert "raiz_biblioteca=_base" in fonte  # arquivo movido também no álbum
    assert "verify_audio_integrity" in fonte


def test_info_mp3_none_e_tratado_como_arquivo_invalido(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import mutagen.mp3

    arquivo = tmp_path / "sem-info.mp3"
    arquivo.write_bytes(b"arquivo")
    monkeypatch.setattr(mutagen.mp3, "MP3", lambda _path: SimpleNamespace(info=None))
    assert reuso._info_do_arquivo(str(arquivo)) is None
