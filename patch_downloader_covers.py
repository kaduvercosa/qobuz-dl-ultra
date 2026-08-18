with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Make _get_extra async
content = content.replace("def _get_extra(", "async def _get_extra(")

# Change tqdm_download to await in _get_extra
content = content.replace(
    '        tqdm_download(\n            item,\n            extra_file,\n            extra,\n            is_parallel=is_parallel,\n            session=session,\n            position_pool=position_pool,\n        )',
    '        await tqdm_download(\n            item,\n            extra_file,\n            extra,\n            is_parallel=is_parallel,\n            session=session,\n            position_pool=position_pool,\n        )'
)

# Make _get_cover_and_embed async
content = content.replace("def _get_cover_and_embed(", "async def _get_cover_and_embed(")

# Change _get_extra calls to await in _get_cover_and_embed
content = content.replace(
    '        _get_extra(\n            item, dirn, extra=saved_name, art_size=saved_art_size,\n            session=session, label="cover art",\n            is_parallel=is_parallel, position_pool=position_pool,\n        )',
    '        await _get_extra(\n            item, dirn, extra=saved_name, art_size=saved_art_size,\n            session=session, label="cover art",\n            is_parallel=is_parallel, position_pool=position_pool,\n        )'
)

content = content.replace(
    '    _get_extra(\n        item, dirn, extra=embed_name, art_size=embedded_art_size,\n        session=session, label="embedded cover art",\n        is_parallel=is_parallel, position_pool=position_pool,\n    )',
    '    await _get_extra(\n        item, dirn, extra=embed_name, art_size=embedded_art_size,\n        session=session, label="embedded cover art",\n        is_parallel=is_parallel, position_pool=position_pool,\n    )'
)

# Make _download_goodies async
content = content.replace("def _download_goodies(", "async def _download_goodies(")

content = content.replace(
    '            _get_extra(\n                goody.get("url"),\n                dirn,\n                extra=goody_name,\n                session=session,\n                label="booklet PDF",\n                is_parallel=is_parallel,\n                position_pool=position_pool,\n            )',
    '            await _get_extra(\n                goody.get("url"),\n                dirn,\n                extra=goody_name,\n                session=session,\n                label="booklet PDF",\n                is_parallel=is_parallel,\n                position_pool=position_pool,\n            )'
)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
