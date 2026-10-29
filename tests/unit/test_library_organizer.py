from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from qobuz_dl import library_organizer as organizer

pytestmark = pytest.mark.unit


def _attrs(title="Minha faixa"):
    return {
        "track_title": title,
        "track_title_base": title,
        "album_title": "Meu álbum",
        "album_title_base": "Meu álbum",
        "album_artist": "Artista",
        "track_artist": "Artista",
        "artist": "Artista",
        "track_number": "01",
        "disc_number": "01",
        "disc_count": "1",
        "year": "2020",
        "ExplicitFlag": "",
    }


def _preparar(root: Path, name="faixa.flac") -> Path:
    old = root / "pasta antiga"
    old.mkdir(parents=True)
    audio = old / name
    audio.write_bytes(b"audio dummy")
    audio.with_suffix(".lrc").write_text("[00:01.00]teste\n", encoding="utf-8")
    return audio


def _organizar(root: Path, config: Path, **kwargs):
    return organizer.organize_directory(
        str(root),
        folder_format="{album_artist}/{album_title}",
        track_format="{track_number} - {track_title}",
        multiple_disc_track_format="{disc_number}.{track_number} - {track_title}",
        journal_base=str(config),
        **kwargs,
    )


def test_dry_run_lista_movimentos_sem_tocar_nos_arquivos(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    output = []
    monkeypatch.setattr(organizer.ui, "emit", output.append)
    monkeypatch.setattr(organizer.ui, "step", output.append)
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = _organizar(root, tmp_path / "config", dry_run=True)

    assert result["planned"] == 2
    assert result["moved"] == 0
    assert result["tracks"] == 1
    assert result["sidecars"] == 1
    assert source.exists()
    assert source.with_suffix(".lrc").exists()
    assert not (tmp_path / "config").exists()
    assert any("1 faixa(s) e 1 arquivo(s) acompanhante(s)" in line for line in output)
    assert any(
        "pasta antiga" in line and "Artista/Meu álbum" in line for line in output
    )
    assert not any("faixa.lrc" in line for line in output)
    assert all(str(root) not in line for line in output)


def test_dry_run_show_files_exibe_detalhes_relativos(tmp_path, monkeypatch):
    root = tmp_path / "music"
    _preparar(root)
    output = []
    monkeypatch.setattr(organizer.ui, "emit", output.append)
    monkeypatch.setattr(organizer.ui, "step", output.append)
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    _organizar(root, tmp_path / "config", dry_run=True, show_files=True)

    assert any("pasta antiga/faixa.flac" in line for line in output)
    assert any("pasta antiga/faixa.lrc" in line for line in output)
    assert all(str(root) not in line for line in output)


def test_nao_acrescenta_marcador_explicito_ausente_do_nome_original(
    tmp_path, monkeypatch
):
    root = tmp_path / "music"
    source = root / "Artista" / "Meu álbum" / "08 - Erro Favorito.flac"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio dummy")
    attrs = _attrs("Erro Favorito 🅴")
    attrs["track_number"] = "08"
    attrs["ExplicitFlag"] = ""
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: attrs)

    result = _organizar(root, tmp_path / "config", dry_run=True)

    assert result["planned"] == 0
    assert source.exists()
    assert not (source.parent / "08 - Erro Favorito 🅴.flac").exists()


def test_arquivos_ocultos_e_assets_da_pasta_acompanham_o_album(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    report = source.parent / ".qobuz-report.json"
    report.write_text('{"album": "Meu álbum"}\n', encoding="utf-8")
    cover = source.parent / "art" / "cover.jpg"
    cover.parent.mkdir()
    cover.write_bytes(b"cover")
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    original_snapshot = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    preview = _organizar(root, tmp_path / "config", dry_run=True, show_files=True)
    assert preview["tracks"] == 1
    assert preview["sidecars"] == 3
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == original_snapshot

    backup = tmp_path / "music-backup-before-organize"
    shutil.copytree(root, backup)
    backup_snapshot = {
        path.relative_to(backup).as_posix(): path.read_bytes()
        for path in backup.rglob("*")
        if path.is_file()
    }
    assert backup_snapshot == original_snapshot

    result = _organizar(root, tmp_path / "config")
    destination = root / "Artista" / "Meu álbum"
    assert result["moved"] == 4
    assert (
        (destination / ".qobuz-report.json").read_text(encoding="utf-8").startswith("{")
    )
    assert (destination / "art" / "cover.jpg").read_bytes() == b"cover"

    undone = organizer.undo_run(str(tmp_path / "config"), result["run_id"])
    assert undone["restored"] == 4
    assert report.exists() and cover.exists() and source.exists()
    restored_snapshot = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert restored_snapshot == backup_snapshot


def test_resumo_cli_separa_alterados_sem_mudancas_e_pulados(tmp_path, monkeypatch):
    root = tmp_path / "music"
    _preparar(root)
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())
    output = []
    for name in ("emit", "step", "ok", "warn", "error"):
        monkeypatch.setattr(organizer.ui, name, output.append)
    arguments = SimpleNamespace(
        undo_list=False,
        undo=None,
        DIR=None,
        dry_run=False,
        no_undo=False,
        include_playlists=False,
        show_files=False,
    )

    status = organizer.cmd_organize(
        arguments,
        directory=str(root),
        journal_base=str(tmp_path / "config"),
        folder_format="{album_artist}/{album_title}",
        track_format="{track_number} - {track_title}",
        multiple_disc_track_format="{disc_number}.{track_number} - {track_title}",
    )
    text = "\n".join(output)

    assert status == 0
    assert "Organização concluída." in text
    assert "Faixas analisadas: 1" in text
    assert "Faixas movidas: 1" in text
    assert "Faixas sem alterações: 0" in text
    assert "Álbuns/pastas alterados" in text
    assert "Sem alterações" in text
    assert "Pulados / conflitos" in text
    assert "Nenhum." in text
    assert "Para desfazer: qobuz-dl organize --undo" in text


def test_move_audio_e_lrc_pode_ser_desfeito(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    output = []
    monkeypatch.setattr(organizer.ui, "emit", output.append)
    monkeypatch.setattr(organizer.ui, "step", output.append)
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = _organizar(root, tmp_path / "config")
    destination = root / "Artista" / "Meu álbum" / "01 - Minha faixa.flac"
    sidecar = destination.with_suffix(".lrc")

    assert result["moved"] == 2
    assert result["run_id"]
    assert destination.read_bytes() == b"audio dummy"
    assert sidecar.read_text(encoding="utf-8") == "[00:01.00]teste\n"
    assert not source.exists()
    assert all(str(root) not in line for line in output)

    undone = organizer.undo_run(str(tmp_path / "config"), result["run_id"])
    assert undone["restored"] == 2
    assert source.read_bytes() == b"audio dummy"
    assert source.with_suffix(".lrc").exists()
    assert not destination.exists()


def test_colisao_de_destino_nao_sobrescreve_arquivo(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    target = root / "Artista" / "Meu álbum" / "01 - Minha faixa.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"arquivo existente")
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = _organizar(root, tmp_path / "config")

    assert result["moved"] == 0
    assert result["errors"] >= 1
    assert source.read_bytes() == b"audio dummy"
    assert target.read_bytes() == b"arquivo existente"


def test_formato_nao_pode_escapar_da_pasta_da_biblioteca(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks indisponíveis neste sistema")
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = organizer.organize_directory(
        str(root),
        folder_format="link/{album_artist}",
        track_format="{track_number} - {track_title}",
        multiple_disc_track_format="{disc_number}.{track_number} - {track_title}",
        journal_base=str(tmp_path / "config"),
        dry_run=True,
    )

    assert result["planned"] == 0
    assert result["errors"] == 1
    assert source.exists()


def test_undo_last_resolve_journal_mais_recente(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root)
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = _organizar(root, tmp_path / "config")
    undone = organizer.undo_run(str(tmp_path / "config"), "last")

    assert result["run_id"]
    assert undone["restored"] == 2
    assert source.exists()


def test_playlist_e_preservada_por_padrao(tmp_path, monkeypatch):
    root = tmp_path / "music"
    source = _preparar(root / "Playlist" / "Mix")
    monkeypatch.setattr(organizer, "metadata_for_file", lambda _path: _attrs())

    result = _organizar(root, tmp_path / "config", dry_run=True)

    assert result["planned"] == 0
    assert source.exists()
