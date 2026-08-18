with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Replace run_in_executor for _get_cover_and_embed (album)
old_cover_album = """                await loop.run_in_executor(
                    None,
                    lambda: _get_cover_and_embed(
                        album_meta["image"]["large"],
                        dirn,
                        save_cover=not self.settings.no_cover,
                        embed_art=self.settings.embed_art,
                        saved_name="cover.jpg",
                        embed_name=EMB_COVER_NAME,
                        saved_art_size=self.settings.saved_art_size,
                        embedded_art_size=self.settings.embedded_art_size,
                        session=self.http_session,
                        is_parallel=is_parallel,
                        position_pool=position_pool,
                    ),
                )"""
new_cover_album = """                await _get_cover_and_embed(
                    album_meta["image"]["large"],
                    dirn,
                    save_cover=not self.settings.no_cover,
                    embed_art=self.settings.embed_art,
                    saved_name="cover.jpg",
                    embed_name=EMB_COVER_NAME,
                    saved_art_size=self.settings.saved_art_size,
                    embedded_art_size=self.settings.embedded_art_size,
                    session=self.http_session,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                )"""
content = content.replace(old_cover_album, new_cover_album)

# Replace run_in_executor for _download_goodies
old_goodies = """                await loop.run_in_executor(
                    None,
                    lambda: _download_goodies(
                        album_meta,
                        dirn,
                        session=self.http_session,
                        is_parallel=is_parallel,
                        position_pool=position_pool,
                    ),
                )"""
new_goodies = """                await _download_goodies(
                    album_meta,
                    dirn,
                    session=self.http_session,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                )"""
content = content.replace(old_goodies, new_goodies)

# Replace run_in_executor for _get_cover_and_embed (track)
old_cover_track = """                    await loop.run_in_executor(
                        None,
                        lambda: _get_cover_and_embed(
                            track_meta["album"]["image"]["large"],
                            dirn,
                            save_cover=save_cover_now,
                            embed_art=self.settings.embed_art,
                            saved_name="cover.jpg",
                            embed_name=(embed_cover_path and os.path.basename(embed_cover_path)) or "",
                            saved_art_size=self.settings.saved_art_size,
                            embedded_art_size=self.settings.embedded_art_size,
                            session=self.http_session,
                            is_parallel=is_parallel,
                            position_pool=position_pool,
                        ),
                    )"""
new_cover_track = """                    await _get_cover_and_embed(
                        track_meta["album"]["image"]["large"],
                        dirn,
                        save_cover=save_cover_now,
                        embed_art=self.settings.embed_art,
                        saved_name="cover.jpg",
                        embed_name=(embed_cover_path and os.path.basename(embed_cover_path)) or "",
                        saved_art_size=self.settings.saved_art_size,
                        embedded_art_size=self.settings.embedded_art_size,
                        session=self.http_session,
                        is_parallel=is_parallel,
                        position_pool=position_pool,
                    )"""
content = content.replace(old_cover_track, new_cover_track)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
