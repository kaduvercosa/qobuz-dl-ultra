with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Replace tqdm_download run_in_executor
old_tqdm_download_call = """                        await loop.run_in_executor(
                            None,
                            lambda: tqdm_download(
                                fresh_track_dict["url"],
                                filename,
                                desc,
                                is_parallel=is_parallel,
                                session=self.http_session,
                                position_pool=position_pool,
                            ),
                        )"""
new_tqdm_download_call = """                        await tqdm_download(
                            fresh_track_dict["url"],
                            filename,
                            desc,
                            is_parallel=is_parallel,
                            session=self.http_session,
                            position_pool=position_pool,
                        )"""
content = content.replace(old_tqdm_download_call, new_tqdm_download_call)

# Replace tqdm_download_segments run_in_executor
old_tqdm_segments_call = """                    await loop.run_in_executor(
                        None,
                        lambda: tqdm_download_segments(
                            fresh_track_dict,
                            filename,
                            desc,
                            is_parallel=is_parallel,
                            session=self.http_session,
                            segment_workers=getattr(
                                self.settings, "segment_workers", None
                            ),
                            position_pool=position_pool,
                        ),
                    )"""
new_tqdm_segments_call = """                    await tqdm_download_segments(
                        fresh_track_dict,
                        filename,
                        desc,
                        is_parallel=is_parallel,
                        session=self.http_session,
                        segment_workers=getattr(
                            self.settings, "segment_workers", None
                        ),
                        position_pool=position_pool,
                    )"""
content = content.replace(old_tqdm_segments_call, new_tqdm_segments_call)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
