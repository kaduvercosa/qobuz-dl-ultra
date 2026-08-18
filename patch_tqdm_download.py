with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Make tqdm_download async
content = content.replace("def tqdm_download(", "async def tqdm_download(")

# Update requests logic to aiohttp logic
# Replace session handling
content = content.replace("owns_session = session is None\n    http = session or requests.Session()",
    "owns_session = session is None\n    http = session or aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=200))")

content = content.replace(
    'r = http.get(\n                    url,\n                    allow_redirects=True,\n                    stream=True,\n                    headers=headers,\n                    timeout=(10, 60),\n                )',
    'r = await http.get(\n                    url,\n                    allow_redirects=True,\n                    headers=headers,\n                    timeout=aiohttp.ClientTimeout(total=60, connect=10),\n                )'
)

# Replace content-length access
content = content.replace('r.headers.get("content-length", 0)', 'r.headers.get("content-length", 0)') # unchanged, still works

# Replace open with aiofiles.open and iteration
old_iter = """                with open(fname, mode) as file, tqdm(
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=tqdm_desc,
                    initial=downloaded_size,
                    bar_format=b_format,
                    position=position,
                    leave=False,
                    ncols=ncols,
                    dynamic_ncols=dynamic_ncols,
                    disable=is_parallel,
                ) as bar:

                    for data in r.iter_content(chunk_size=65536):
                        if abort_event.is_set():
                            return
                        if data:
                            size = file.write(data)
                            downloaded_size += size
                            bar.update(size)"""

new_iter = """                bar = tqdm(
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=tqdm_desc,
                    initial=downloaded_size,
                    bar_format=b_format,
                    position=position,
                    leave=False,
                    ncols=ncols,
                    dynamic_ncols=dynamic_ncols,
                    disable=is_parallel,
                )
                async with aiofiles.open(fname, mode) as file:
                    async for data in r.content.iter_chunked(1048576):
                        if abort_event.is_set():
                            bar.close()
                            return
                        if data:
                            size = await file.write(data)
                            downloaded_size += size
                            bar.update(size)
                bar.close()"""

content = content.replace(old_iter, new_iter)

# r status code is aiohttp r.status instead of r.status_code? No, r.status for aiohttp.
content = content.replace("if r.status_code == 416:", "if r.status == 416:")
content = content.replace("if r.status_code not in [200, 206]:", "if r.status not in [200, 206]:")
content = content.replace('raise Exception(f"Status Server: {r.status_code}")', 'raise Exception(f"Status Server: {r.status}")')

# The `attempt < max_retries - 1:` loop wait
content = content.replace("time.sleep(wait)", "await asyncio.sleep(wait)")

# Handle http.close() in finally block
content = content.replace("http.close()", "await http.close()")

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
