import os
from qobuz_dl.constants import (
    DEFAULT_FOLDER,
    DEFAULT_TRACK,
    DEFAULT_MULTIPLE_DISC_TRACK,
)


class QobuzDLSettings:
    """
    Central configuration object for Qobuz-DL Ultimate Edition.

    Maps and stores all user preferences, CLI flags, and config.ini variables.
    This object dictates the behavior of the Downloader, Tagger, and Sync engines,
    enforcing the 'Zero Hardcoding' philosophy across the entire application.
    """

    def __init__(self, **kwargs):
        """
        Initializes the configuration settings.

        Args:
            **kwargs: Arbitrary keyword arguments containing all parsed configuration
                values (e.g., tagging flags, folder formats, thread counts).
        """
        # basic options
        self.email = kwargs.get("email")
        self.password = kwargs.get("password")
        self.default_folder = kwargs.get("default_folder", "QobuzDownloads")
        self.default_quality = kwargs.get("default_quality", 6)
        self.default_limit = kwargs.get("default_limit", 20)
        self.no_m3u = kwargs.get("no_m3u", False)
        self.albums_only = kwargs.get("albums_only", False)
        self.no_fallback = not kwargs.get("no_fallback", False)
        self.no_database = kwargs.get("no_database", False)
        self.app_id = kwargs.get("app_id")
        self.secrets = kwargs.get("secrets")
        self.folder_format = kwargs.get("folder_format")
        self.fallback_folder_format = kwargs.get(
            "fallback_folder_format", DEFAULT_FOLDER
        )
        self.track_format = kwargs.get("track_format")
        self.smart_discography = kwargs.get("smart_discography", False)
        self.dry_run = kwargs.get("dry_run", False)
        self.tag_only = kwargs.get("tag_only", False)
        self.musicbrainz = kwargs.get("musicbrainz", False)
        # Normaliza datas: aceita "YYYY" (expande pra "YYYY-01-01" / "YYYY-12-31")
        # e "YYYY-MM-DD" direto. None desativa o filtro.
        _since = kwargs.get("since_date") or ""
        self.since_date = (
            (_since[:4] + "-01-01") if len(_since) == 4 else (_since or None)
        )
        _before = kwargs.get("before_date") or ""
        self.before_date = (
            (_before[:4] + "-12-31") if len(_before) == 4 else (_before or None)
        )
        self.legacy_charmap = kwargs.get("legacy_charmap", False)

        # Verificacao de integridade pos-download (decodifica cada FLAC/MP3
        # final com ffmpeg pra pegar corrupcao real, nao so' tag). Off por
        # padrao porque adiciona tempo real a cada faixa -- em discografias
        # grandes isso soma. Ver qobuz_dl/utils.py:verify_audio_integrity()
        # e o uso em downloader.py.
        self.verify_after_download = kwargs.get("verify_after_download", False)

        # Emite uma linha JSON por evento de progresso em vez de (ou alem
        # de) barra de progresso tqdm no terminal. Pensado pra frontends
        # (GUI web, app) conseguirem acompanhar o progresso sem precisar
        # fazer parsing de saida de terminal com barras ANSI.
        self.progress_json = kwargs.get("progress_json", False)

        # tag options
        self.no_album_artist_tag = kwargs.get("no_album_artist_tag", False)
        self.no_album_title_tag = kwargs.get("no_album_title_tag", False)
        self.no_track_artist_tag = kwargs.get("no_track_artist_tag", False)
        self.no_track_title_tag = kwargs.get("no_track_title_tag", False)
        self.no_release_date_tag = kwargs.get("no_release_date_tag", False)
        self.no_media_type_tag = kwargs.get("no_media_type_tag", False)
        self.no_genre_tag = kwargs.get("no_genre_tag", False)
        self.no_track_number_tag = kwargs.get("no_track_number_tag", False)
        self.no_track_total_tag = kwargs.get("no_track_total_tag", False)
        self.no_disc_number_tag = kwargs.get("no_disc_number_tag", False)
        self.no_disc_total_tag = kwargs.get("no_disc_total_tag", False)
        self.no_composer_tag = kwargs.get("no_composer_tag", False)
        self.no_conductor_tag = kwargs.get("no_conductor_tag", False)
        self.no_ensemble_tag = kwargs.get("no_ensemble_tag", False)
        self.no_work_tag = kwargs.get("no_work_tag", False)
        self.no_explicit_tag = kwargs.get("no_explicit_tag", False)
        self.no_copyright_tag = kwargs.get("no_copyright_tag", False)
        self.no_label_tag = kwargs.get("no_label_tag", False)
        self.no_upc_tag = kwargs.get("no_upc_tag", False)
        self.no_isrc_tag = kwargs.get("no_isrc_tag", False)
        self.no_replaygain_tag = kwargs.get("no_replaygain_tag", False)
        self.no_album_url_tag = kwargs.get("no_album_url_tag", False)
        self.lrc_files = kwargs.get("lrc_files", True)
        self.embed_lyrics = kwargs.get("embed_lyrics", True)
        self.multi_value_tags = kwargs.get("multi_value_tags", False)

        # cover options
        self.embed_art = kwargs.get("embed_art", False)
        self.cover_og_quality = kwargs.get("og_cover", False)
        self.no_cover = kwargs.get("no_cover", False)
        self.embedded_art_size = kwargs.get("embedded_art_size", "org")
        self.saved_art_size = kwargs.get("saved_art_size", "org")

        # multiple disc option
        self.multiple_disc_prefix = kwargs.get("multiple_disc_prefix", "CD")
        self.multiple_disc_one_dir = kwargs.get("multiple_disc_one_dir", False)
        self.multiple_disc_track_format = kwargs.get(
            "multiple_disc_track_format", DEFAULT_MULTIPLE_DISC_TRACK
        )

        # Add parallel download thread count option
        self.max_workers = int(kwargs.get("max_workers", 1))

        # Threads usados DENTRO do fallback de download segmentado (uma
        # unica faixa, quando o CDN normal bloqueia/Akamai). Antes era um
        # ThreadPoolExecutor(max_workers=8) fixo no codigo, igual em
        # qualquer dispositivo. "0" (ou nao configurado) = auto-detect com
        # base em os.cpu_count(), com teto de 8 pra nao sobrecarregar
        # dispositivos mais fracos (ex: iPhone rodando A-Shell) nem abrir
        # conexoes demais a toa em desktops com muitos nucleos.
        segment_workers_raw = int(kwargs.get("segment_workers", 0) or 0)
        if segment_workers_raw > 0:
            self.segment_workers = segment_workers_raw
        else:
            self.segment_workers = min(8, max(2, (os.cpu_count() or 4) * 2))

        # user_auth_token
        self.user_auth_token = kwargs.get("user_auth_token", "")

    @staticmethod
    def from_arguments_configparser(arguments, config):
        """
        Factory method to construct a QobuzDLSettings object by merging command-line
        arguments with the config.ini file. CLI arguments inherently take precedence
        over config.ini values.

        Args:
            arguments (argparse.Namespace): Parsed command line arguments from the CLI.
            config (configparser.ConfigParser): The initialized ConfigParser object.

        Returns:
            QobuzDLSettings: The fully resolved configuration object.
        """
        # Determine the correct section to read from config.ini
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"

        # basic options
        kwargs = {
            "email": config.get(section, "email", fallback=""),
            "password": config.get(section, "password", fallback=""),
            "default_folder": getattr(arguments, "directory", None) or
            config.get(section, "default_folder", fallback="QobuzDownloads"),
            "default_quality": getattr(arguments, "quality", None) or
            config.get(section, "default_quality", fallback="6"),
            "default_limit": config.get(section, "default_limit", fallback="20"),
            "no_m3u": getattr(arguments, "no_m3u", False) or
            config.getboolean(section, "no_m3u", fallback=False),
            "albums_only": getattr(arguments, "albums_only", False) or
            config.getboolean(section, "albums_only", fallback=False),
            "no_fallback": getattr(arguments, "no_fallback", False) or
            config.getboolean(section, "no_fallback", fallback=False),
            "no_database": getattr(arguments, "no_db", False) or
            config.getboolean(section, "no_database", fallback=False),
            "app_id": config.get(section, "app_id", fallback=""),
            "secrets": [
                s for s in config.get(section, "secrets", fallback="").split(",") if s
            ],
            "folder_format": getattr(arguments, "folder_format", None) or
            config.get(section, "folder_format", fallback=DEFAULT_FOLDER),
            "fallback_folder_format": getattr(arguments, "fallback_folder_format", None) or
            config.get(section, "fallback_folder_format", fallback=DEFAULT_FOLDER),
            "track_format": getattr(arguments, "track_format", None) or
            config.get(section, "track_format", fallback=DEFAULT_TRACK),
            "smart_discography": getattr(arguments, "smart_discography", False) or
            config.getboolean(section, "smart_discography", fallback=False),
            "dry_run": getattr(arguments, "dry_run", False),
            "tag_only": getattr(arguments, "tag_only", False),
            "musicbrainz": getattr(arguments, "musicbrainz", False),
            "since_date": getattr(arguments, "since", None) or
            config.get(section, "since_date", fallback="") or
            None,
            "before_date": getattr(arguments, "before", None) or
            config.get(section, "before_date", fallback="") or
            None,
            "verify_after_download": getattr(arguments, "verify_after_download", False) or
            config.getboolean(section, "verify_after_download", fallback=False),
            "progress_json": getattr(arguments, "progress_json", False) or
            config.getboolean(section, "progress_json", fallback=False),
            # cover options
            "embed_art": getattr(arguments, "embed_art", None) or
            config.getboolean(section, "embed_art", fallback=True),
            "og_cover": getattr(arguments, "og_cover", None) or
            config.getboolean(section, "og_cover", fallback=False),
            "no_cover": getattr(arguments, "no_cover", False) or
            config.getboolean(section, "no_cover", fallback=False),
            "embedded_art_size": getattr(arguments, "embedded_art_size", None) or
            config.get(section, "embedded_art_size", fallback="org"),
            "saved_art_size": getattr(arguments, "saved_art_size", None) or
            config.get(section, "saved_art_size", fallback="org"),
            # multiple disc option
            "multiple_disc_prefix": getattr(arguments, "multiple_disc_prefix", None) or
            config.get(section, "multiple_disc_prefix", fallback="CD"),
            "multiple_disc_one_dir": getattr(arguments, "multiple_disc_one_dir", False) or
            config.getboolean(section, "multiple_disc_one_dir", fallback=False),
            "multiple_disc_track_format": getattr(
                arguments, "multiple_disc_track_format", None
            ) or
            config.get(
                section,
                "multiple_disc_track_format",
                fallback=DEFAULT_MULTIPLE_DISC_TRACK,
            ),
            # tag options
            "no_album_artist_tag": getattr(arguments, "no_album_artist_tag", False) or
            config.getboolean(section, "no_album_artist_tag", fallback=False),
            "no_album_title_tag": getattr(arguments, "no_album_title_tag", False) or
            config.getboolean(section, "no_album_title_tag", fallback=False),
            "no_track_artist_tag": getattr(arguments, "no_track_artist_tag", False) or
            config.getboolean(section, "no_track_artist_tag", fallback=False),
            "no_track_title_tag": getattr(arguments, "no_track_title_tag", False) or
            config.getboolean(section, "no_track_title_tag", fallback=False),
            "no_release_date_tag": getattr(arguments, "no_release_date_tag", False) or
            config.getboolean(section, "no_release_date_tag", fallback=False),
            "no_media_type_tag": getattr(arguments, "no_media_type_tag", False) or
            config.getboolean(section, "no_media_type_tag", fallback=False),
            "no_genre_tag": getattr(arguments, "no_genre_tag", False) or
            config.getboolean(section, "no_genre_tag", fallback=False),
            "no_track_number_tag": getattr(arguments, "no_track_number_tag", False) or
            config.getboolean(section, "no_track_number_tag", fallback=False),
            "no_track_total_tag": getattr(arguments, "no_track_total_tag", False) or
            config.getboolean(section, "no_track_total_tag", fallback=False),
            "no_disc_number_tag": getattr(arguments, "no_disc_number_tag", False) or
            config.getboolean(section, "no_disc_number_tag", fallback=False),
            "no_disc_total_tag": getattr(arguments, "no_disc_total_tag", False) or
            config.getboolean(section, "no_disc_total_tag", fallback=False),
            "no_composer_tag": getattr(arguments, "no_composer_tag", False) or
            config.getboolean(section, "no_composer_tag", fallback=False),
            "no_conductor_tag": getattr(arguments, "no_conductor_tag", False) or
            config.getboolean(section, "no_conductor_tag", fallback=False),
            "no_ensemble_tag": getattr(arguments, "no_ensemble_tag", False) or
            config.getboolean(section, "no_ensemble_tag", fallback=False),
            "no_work_tag": getattr(arguments, "no_work_tag", False) or
            config.getboolean(section, "no_work_tag", fallback=False),
            "no_explicit_tag": getattr(arguments, "no_explicit_tag", False) or
            config.getboolean(section, "no_explicit_tag", fallback=False),
            "no_copyright_tag": getattr(arguments, "no_copyright_tag", False) or
            config.getboolean(section, "no_copyright_tag", fallback=False),
            "no_label_tag": getattr(arguments, "no_label_tag", False) or
            config.getboolean(section, "no_label_tag", fallback=False),
            "no_upc_tag": getattr(arguments, "no_upc_tag", False) or
            config.getboolean(section, "no_upc_tag", fallback=False),
            "no_isrc_tag": getattr(arguments, "no_isrc_tag", False) or
            config.getboolean(section, "no_isrc_tag", fallback=False),
            "no_replaygain_tag": getattr(arguments, "no_replaygain_tag", False) or
            config.getboolean(section, "no_replaygain_tag", fallback=False),
            "no_album_url_tag": getattr(arguments, "no_album_url_tag", False) or
            config.getboolean(section, "no_album_url_tag", fallback=False),
            # Add parallel download thread count configuration
            "max_workers": getattr(arguments, "max_workers", None) or
            config.get(section, "max_workers", fallback="1"),
            # Threads do fallback de download segmentado -- "0"/ausente =
            # auto-detect adaptativo por dispositivo (ver settings.py).
            "segment_workers": getattr(arguments, "segment_workers", None) or
            config.get(section, "segment_workers", fallback="0"),
            # user_auth_token
            "user_auth_token": config.get(section, "user_auth_token", fallback=""),
            "lrc_files": not getattr(
                arguments,
                "no_lrc_files",
                config.getboolean(section, "no_lrc_files", fallback=False),
            ),
            "embed_lyrics": (
                False
                if getattr(arguments, "no_embed_lyrics", False)
                else config.getboolean(section, "embed_lyrics", fallback=True)
            ),
            "multi_value_tags": (
                False
                if getattr(arguments, "no_multi_tags", False)
                else getattr(
                    arguments,
                    "multi_value_tags",
                    config.getboolean(section, "multi_value_tags", fallback=False),
                )
            ),
        }

        return QobuzDLSettings(**kwargs)
