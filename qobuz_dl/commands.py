import argparse
import os


def _default_download_folder():
    """
    Resolves the fallback download folder.

    On iOS (a-Shell), QOBUZ_DL_IOS_HOME must be set (e.g. to $HOME/Documents),
    since that's the only folder the Files app can see. Everywhere else this
    is unset and the historical relative "QobuzDownloads" default is kept.
    """
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home:
        return os.path.join(ios_home, "QobuzDownloads")
    return "QobuzDownloads"


def fun_args(subparsers, default_limit):
    """Configures the 'interactive' command-line subparser."""
    interactive = subparsers.add_parser(
        "interactive",
        description="Pesquise interativamente por faixas e álbuns.",
        help="Modo interativo",
        aliases=["i", "fun"],
    )
    interactive.add_argument(
        "-l",
        "--limit",
        metavar="int",
        default=default_limit,
        help="limit of search results (default: 20)",
    )
    return interactive


def lucky_args(subparsers):
    """Configures the 'lucky' command-line subparser."""
    lucky = subparsers.add_parser(
        "lucky",
        description="Download the first <n> albums returned from a Qobuz search.",
        help="lucky mode",
    )
    lucky.add_argument(
        "-t",
        "--type",
        default="album",
        help="type of items to search (artist, album, track, playlist) (default: album)",
    )
    lucky.add_argument(
        "-n",
        "--number",
        metavar="int",
        type=int,
        default=1,
        help="number of results to download (default: 1)",
    )
    lucky.add_argument("QUERY", nargs="+", help="search query")
    return lucky


def dl_args(subparsers):
    """Configures the 'dl' (download) command-line subparser."""
    download = subparsers.add_parser(
        "dl",
        description="Download by album/track/artist/label/playlist/last.fm-playlist URL.",
        help="input mode",
    )
    download.add_argument(
        "SOURCE",
        metavar="SOURCE",
        nargs="+",
        help=("one or more URLs (space separated) or a text file"),
    )
    download.add_argument(
        "-b",
        "--blacklist",
        help="Path to a text file containing keywords to blacklist and skip",
        type=str,
        default=None,
    )
    return download


def lyrics_args(subparsers, default_folder=None):
    """
    Configures the 'lyrics' command-line subparser for retroactive lyrics injection.
    DIR is optional (nargs='?'). If omitted, it automatically resolves to the root
    download directory configured in config.ini.
    """
    lyrics = subparsers.add_parser(
        "lyrics",
        description="Retroactively scan a directory and inject missing lyrics or translations into audio files.",
        help="lyrics injection mode",
    )
    lyrics.add_argument(
        "DIR",
        metavar="DIRECTORY",
        nargs="?",
        default=None,
        help=f'The local directory containing the music files to be scanned (default: from config.ini: "{default_folder}")',
    )
    return lyrics


def sync_playlist_args(subparsers):
    """Configures the 'sync-playlist' command-line subparser."""
    sync_pl = subparsers.add_parser(
        "sync-playlist",
        aliases=["sp"],
        description="Synchronize a local folder with a Qobuz playlist. "
        "Downloads missing tracks and removes tracks no longer in the playlist.",
        help="sync a local folder with a Qobuz playlist",
    )
    sync_pl.add_argument(
        "URL",
        help="Qobuz playlist URL (e.g. https://play.qobuz.com/playlist/12345)",
    )
    sync_pl.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt before deleting/downloading",
    )
    return sync_pl


def add_common_arg(custom_parser, default_folder, default_quality):
    """Appends global arguments to a specific subparser."""
    custom_parser.add_argument(
        "-d",
        "--directory",
        metavar="PATH",
        default=default_folder,
        help=f'directory for downloads (default: "{default_folder}")',
    )
    custom_parser.add_argument(
        "--no-lrc-files",
        dest="lrc_files",
        action="store_false",
        default=argparse.SUPPRESS,
        help="do not save synchronized lyrics to external .lrc files",
    )
    custom_parser.add_argument(
        "-q",
        "--quality",
        metavar="int",
        type=int,
        default=default_quality,
        choices=[5, 6, 7, 27],
        help=(
            'audio "quality" (5, 6, 7, 27)\n'
            f"[320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (default: {default_quality})"
        ),
    )
    custom_parser.add_argument(
        "--albums-only",
        action="store_true",
        help=("don't download singles, EPs and VA releases"),
    )
    custom_parser.add_argument(
        "--no-m3u",
        action="store_true",
        help="don't create .m3u files when downloading playlists",
    )
    custom_parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="disable quality fallback (skip releases not available in set quality)",
    )
    custom_parser.add_argument(
        "--no-db", action="store_true", help="don't call the database"
    )
    custom_parser.add_argument(
        "-ff",
        "--folder-format",
        metavar="PATTERN",
        help="""pattern for formatting folder names, e.g
        "{album_artist} - {album_title} ({year}) {{{barcode}}}". available keys: 
        album_id, album_url, album_title, album_title, album_artist, album_genre, 
        album_composer, label, copyright, upc, barcode, release_date, year, media_type,
        format, bit_depth, sampling_rate, album_version, disc_count, track_count.
        Note1: {album_title}, {track_title} will contain version information if available.
        Note2: {album_title_base}, {track_title_base} will contain only the title,
        Note3: {track_title}, {track_title_base} is only available if the given url is a track url.
        Note4: You can use '/' to create subdirectories.
        Cannot contain characters used by the system, which includes :<>""",
    )
    custom_parser.add_argument(
        "-fbff",
        "--fallback-folder-format",
        metavar="PATTERN",
        help="""fallback pattern for formatting folder names when the main pattern fails.""",
    )
    custom_parser.add_argument(
        "-tf",
        "--track-format",
        metavar="PATTERN",
        help="""pattern for formatting track names. e.g
        "{track_number} - {track_title}" 
        available keys:
        album_title, album_title_base, album_artist, track_id, track_artist, track_composer, 
        track_number, isrc, bit_depth, sampling_rate, track_title, track_title_base
        version, year, disc_number, release_date.
        Cannot contain characters used by the system, which includes /:<>
        """,
    )
    custom_parser.add_argument(
        "-s",
        "--smart-discography",
        action="store_true",
        help="""Try to filter out spam-like albums when requesting an artist's discography.""",
    )
    custom_parser.add_argument(
        "--verify-download",
        dest="verify_after_download",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "decode each downloaded track with ffmpeg right after it finishes "
            "to catch real audio corruption (not just tag/metadata checks). "
            "Adds real time per track -- off by default. See also: "
            "'python check_audio.py --verify-library' to scan an existing "
            "library in bulk instead of during download."
        ),
    )
    custom_parser.add_argument(
        "--progress-json",
        dest="progress_json",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "also emit one JSON line per track-start/track-done event on "
            "stdout, meant for external frontends (web GUI, app) to follow "
            "progress without parsing the tqdm terminal output."
        ),
    )
    custom_parser.add_argument(
        "--delay",
        type=int,
        default=0,
        help="Wait a specified number of seconds between track downloads to prevent server bans.",
    )
    custom_parser.add_argument(
        "--playlist-as-albums",
        action="store_true",
        help="Download playlist items using standard album folder structures instead of a flat playlist directory",
    )
    custom_parser.add_argument(
        "--no-lyrics",
        action="store_true",
        help="disable automatic lyrics fetching and injection for this session",
    )
    custom_parser.add_argument(
        "--no-embed-lyrics",
        dest="no_embed_lyrics",
        action="store_true",
        default=argparse.SUPPRESS,
        help="do not embed lyrics into the audio file tags (save as .lrc/.txt only)",
    )
    custom_parser.add_argument(
        "--multi-tags",
        dest="multi_value_tags",
        action="store_true",
        default=argparse.SUPPRESS,
        help="split comma-separated metadata (like genres) into multiple tag fields",
    )
    custom_parser.add_argument(
        "--no-multi-tags",
        action="store_true",
        help="temporarily disable splitting metadata into multiple tags (overrides config.ini)",
    )
    custom_parser.add_argument(
        "--booklet-only",
        action="store_true",
        help="only download the Digital Booklet and PDF Goodies (skips audio files)",
    )
    custom_parser.add_argument(
        "--native-lang",
        action="store_true",
        help="do not force English; download metadata in the account's native language",
    )
    custom_parser.add_argument(
        "--no-credits",
        action="store_true",
        help="disable the generation of the Digital Booklet.txt (Credits & Review) file",
    )
    custom_parser.add_argument(
        "--with-credits",
        action="store_true",
        help="force the generation of the Digital Booklet.txt (overrides config.ini)",
    )

    tag_group = custom_parser.add_argument_group("tag options")
    tag_group.add_argument(
        "--no-album-artist-tag", action="store_true", help="don't add album artist tag"
    )
    tag_group.add_argument(
        "--no-album-title-tag", action="store_true", help="don't add album title tag"
    )
    tag_group.add_argument(
        "--no-track-artist-tag", action="store_true", help="don't add track artist tag"
    )
    tag_group.add_argument(
        "--no-track-title-tag", action="store_true", help="don't add track title tag"
    )
    tag_group.add_argument(
        "--no-release-date-tag", action="store_true", help="don't add release date tag"
    )
    tag_group.add_argument(
        "--no-media-type-tag", action="store_true", help="don't add media type tag"
    )
    tag_group.add_argument(
        "--no-genre-tag", action="store_true", help="don't add genre tag"
    )
    tag_group.add_argument(
        "--no-replaygain-tag",
        action="store_true",
        help="Do not add ReplayGain tags to the audio files.",
    )
    tag_group.add_argument(
        "--no-album-url-tag",
        action="store_true",
        help="Do not add the QOBUZ ALBUM URL tag to the audio files.",
    )
    tag_group.add_argument(
        "--no-track-number-tag", action="store_true", help="don't add track number tag"
    )
    tag_group.add_argument(
        "--no-track-total-tag", action="store_true", help="don't add total tracks tag"
    )
    tag_group.add_argument(
        "--no-disc-number-tag", action="store_true", help="don't add disc number tag"
    )
    tag_group.add_argument(
        "--no-disc-total-tag", action="store_true", help="don't add total discs tag"
    )
    tag_group.add_argument(
        "--no-composer-tag", action="store_true", help="don't add composer tag"
    )
    tag_group.add_argument(
        "--no-explicit-tag", action="store_true", help="don't add explicit advisory tag"
    )
    tag_group.add_argument(
        "--no-copyright-tag", action="store_true", help="don't add copyright tag"
    )
    tag_group.add_argument(
        "--no-label-tag", action="store_true", help="don't add label tag"
    )
    tag_group.add_argument(
        "--no-upc-tag", action="store_true", help="don't add UPC/barcode tag"
    )
    tag_group.add_argument(
        "--no-isrc-tag", action="store_true", help="don't add ISRC tag"
    )

    artwork_group = custom_parser.add_argument_group("cover artwork options")
    artwork_group.add_argument(
        "-e",
        "--embed-art",
        action="store_true",
        help="embed cover art into audio files",
    )
    artwork_group.add_argument(
        "--og-cover", action="store_true", help="download cover art in original quality"
    )
    artwork_group.add_argument(
        "--no-cover", action="store_true", help="don't download cover art"
    )
    artwork_group.add_argument(
        "--embedded-art-size",
        choices=["50", "100", "150", "300", "600", "max", "org"],
        default=None,
        help="size of embedded artwork (default: org, or config.ini's "
        "embedded_art_size -- if the org file exceeds FLAC's 16MB embed "
        "limit, it's automatically recompressed just enough to fit, "
        "keeping the full-quality file saved on disk untouched)",
    )
    artwork_group.add_argument(
        "--saved-art-size",
        choices=["50", "100", "150", "300", "600", "max", "org"],
        default="org",
        help="size of saved artwork (default: org)",
    )

    multiple_disc_group = custom_parser.add_argument_group("multiple disc options")
    multiple_disc_group.add_argument(
        "--multiple-disc-prefix",
        default="CD",
        metavar="PREFIX",
        help="Setting folder prefix for multiple discs album (default: CD)",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-one-dir",
        action="store_true",
        help="store multiple disc releases in one directory",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-track-format",
        metavar="FORMAT",
        help="track format for multiple disc releases",
    )

    parallel_group = custom_parser.add_argument_group("parallel download options")
    parallel_group.add_argument(
        "--max-workers",
        type=int,
        metavar="N",
        help="maximum number of parallel downloads (default: 1 -- sequential; "
        "only kicks in with >1 track queued at once)",
    )
    parallel_group.add_argument(
        "--segment-workers",
        type=int,
        metavar="N",
        help="threads used inside segmented-download fallback",
    )


def qobuz_dl_args(default_quality=6, default_limit=20, default_folder=None):
    """Initializes and constructs the master argument parser for Qobuz-DL."""
    if default_folder is None:
        default_folder = _default_download_folder()

    parser = argparse.ArgumentParser(
        prog="qobuz-dl",
        description=(
            "The ultimate Qobuz music downloader.\nSee usage"
            " examples on https://github.com/Sei969/qobuz-dl"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-r", "--reset", action="store_true", help="create/reset config file"
    )
    parser.add_argument(
        "-p",
        "--purge",
        action="store_true",
        help="purge/delete downloaded-IDs database",
    )
    parser.add_argument(
        "--sync-db",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help="scan local directory to restore missing Qobuz IDs into the database",
    )
    parser.add_argument(
        "--find-duplicates",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help=(
            "scan local directory for duplicate tracks by audio fingerprint "
            "(Chromaprint/AcoustID), not just tags -- requires the 'fpcalc' "
            "binary installed on the system"
        ),
    )
    parser.add_argument(
        "--watch",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help=(
            "watch local directory and automatically run retro-tagging "
            "(lyrics injection) whenever new audio files appear -- runs "
            "until interrupted with Ctrl+C"
        ),
    )
    parser.add_argument(
        "-sc", "--show-config", action="store_true", help="show configuration"
    )

    subparsers = parser.add_subparsers(
        title="commands",
        description="run qobuz-dl <command> --help for more info\n(e.g. qobuz-dl interactive --help)",
        dest="command",
    )

    interactive = fun_args(subparsers, default_limit)
    download = dl_args(subparsers)
    lucky = lucky_args(subparsers)
    lyrics_cmd = lyrics_args(subparsers, default_folder=default_folder)
    sync_pl_cmd = sync_playlist_args(subparsers)

    for i in (interactive, download, lucky, sync_pl_cmd):
        add_common_arg(i, default_folder, default_quality)

    return parser
