with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Make tqdm_download_segments async
content = content.replace("def tqdm_download_segments(", "async def tqdm_download_segments(")

# Replace requests session
content = content.replace("owns_session = session is None\n    http = session or requests.Session()",
    "owns_session = session is None\n    http = session or aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=200))")

# The get_seg_size function inside tqdm_download_segments needs to be async
old_get_seg_size = """    def get_seg_size(seg_num):
        if abort_event.is_set():
            return 0
        url = url_template.replace("$SEGMENT$", str(seg_num))
        try:
            r = http.head(url, timeout=5)
            return int(r.headers.get("content-length", 0))
        except Exception as e:
            # Antes retornava 0 sem dizer nada -- isso distorce silenciosamente
            # o total_size calculado abaixo e a barra de progresso. Logado em
            # debug pra dar pra distinguir "servidor realmente nao informou
            # content-length" de "essa chamada HEAD falhou" quando o total
            # bater estranho.
            logger.debug(f"HEAD falhou para segmento (assumindo tamanho 0): {e}")
            return 0"""

new_get_seg_size = """    async def get_seg_size(seg_num):
        if abort_event.is_set():
            return 0
        url = url_template.replace("$SEGMENT$", str(seg_num))
        try:
            r = await http.head(url, timeout=aiohttp.ClientTimeout(total=5))
            return int(r.headers.get("content-length", 0))
        except Exception as e:
            logger.debug(f"HEAD falhou para segmento (assumindo tamanho 0): {e}")
            return 0"""

content = content.replace(old_get_seg_size, new_get_seg_size)

# Replace ThreadPoolExecutor for get_seg_size with asyncio.gather
old_pool_size = """    total_size = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures_size = [ex.submit(get_seg_size, i) for i in range(n_segments + 1)]
        for f in futures_size:
            while True:
                if abort_event.is_set():
                    return
                try:
                    total_size += f.result(timeout=1.0)
                    break
                except concurrent.futures.TimeoutError:
                    continue"""

new_pool_size = """    total_size = 0
    tasks_size = [get_seg_size(i) for i in range(n_segments + 1)]
    results = await asyncio.gather(*tasks_size)
    if abort_event.is_set():
        return
    total_size = sum(results)"""

content = content.replace(old_pool_size, new_pool_size)

# Replace fetch_segment_fluid to be async
old_fetch = """    def fetch_segment_fluid(seg_num):
        if abort_event.is_set():
            return bytearray()
        url = url_template.replace("$SEGMENT$", str(seg_num))
        r = http.get(url, stream=True, timeout=15)
        r.raise_for_status()
        seg_data = bytearray()

        for chunk in r.iter_content(chunk_size=65536):
            if abort_event.is_set():
                return bytearray()
            seg_data.extend(chunk)
            bar.update(len(chunk))
        return seg_data"""

new_fetch = """    async def fetch_segment_fluid(seg_num):
        if abort_event.is_set():
            return bytearray()
        url = url_template.replace("$SEGMENT$", str(seg_num))
        r = await http.get(url, timeout=aiohttp.ClientTimeout(total=15))
        r.raise_for_status()
        seg_data = bytearray()

        async for chunk in r.content.iter_chunked(1048576):
            if abort_event.is_set():
                return bytearray()
            seg_data.extend(chunk)
            bar.update(len(chunk))
        return seg_data"""

content = content.replace(old_fetch, new_fetch)

# Replace write loop for segments
old_write_loop = """    try:
        with open(tmp_fname, "wb") as file, tqdm(
            total=total_size,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            desc=tqdm_desc,
            bar_format=b_format,
            position=position,
            leave=False,
            ncols=ncols,
            dynamic_ncols=dynamic_ncols,
            disable=is_parallel,
        ) as bar:

            segment_uuid = None
            for i in range(2):
                seg_data = fetch_segment_fluid(i)
                if abort_event.is_set():
                    return
                if i == 1:
                    segment_uuid = _get_qobuz_segment_uuid(seg_data)
                    if segment_uuid is None:
                        raise ConnectionError(f"Cannot find segment UUID for {fname}")

                file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

            if n_segments >= 2:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures_seg = [
                        executor.submit(fetch_segment_fluid, i)
                        for i in range(2, n_segments + 1)
                    ]
                    for f in futures_seg:
                        while True:
                            if abort_event.is_set():
                                return
                            try:
                                seg_data = f.result(timeout=1.0)
                                break
                            except concurrent.futures.TimeoutError:
                                continue
                        if not abort_event.is_set():
                            file.write(
                                _decrypt_qobuz_segment(seg_data, raw_key, segment_uuid)
                            )"""

new_write_loop = """    try:
        bar = tqdm(
            total=total_size,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            desc=tqdm_desc,
            bar_format=b_format,
            position=position,
            leave=False,
            ncols=ncols,
            dynamic_ncols=dynamic_ncols,
            disable=is_parallel,
        )
        async with aiofiles.open(tmp_fname, "wb") as file:
            segment_uuid = None
            for i in range(2):
                seg_data = await fetch_segment_fluid(i)
                if abort_event.is_set():
                    bar.close()
                    return
                if i == 1:
                    segment_uuid = _get_qobuz_segment_uuid(seg_data)
                    if segment_uuid is None:
                        raise ConnectionError(f"Cannot find segment UUID for {fname}")

                await file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

            if n_segments >= 2:
                # To limit concurrency but fetch concurrently, we can use a semaphore or gather batches
                sem = asyncio.Semaphore(workers)

                async def fetch_and_write(i):
                    async with sem:
                        seg_data = await fetch_segment_fluid(i)
                        return seg_data

                # Note: To write in order, we need to gather or wait for them in order
                tasks_seg = [fetch_and_write(i) for i in range(2, n_segments + 1)]
                for coroutine in asyncio.as_completed(tasks_seg):
                    if abort_event.is_set():
                        break
                    # Wait, as_completed yields out of order!
                    # For audio segments, writing them out of order to the file will corrupt the FLAC file unless we seek.
                    # Since we don't know the exact size of decrypted segments beforehand, we must decrypt and write sequentially.
                    pass

                # Therefore, we fetch concurrently but write sequentially.
                tasks_seg = [asyncio.create_task(fetch_and_write(i)) for i in range(2, n_segments + 1)]
                for task in tasks_seg:
                    seg_data = await task
                    if not abort_event.is_set():
                        await file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

        bar.close()"""

content = content.replace(old_write_loop, new_write_loop)

# Fix ffmpeg subprocess call (asyncio.create_subprocess_exec instead of subprocess.run)
old_ffmpeg = """        remux = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                tmp_fname,
                "-c:a",
                "copy",
                "-f",
                "flac",
                fname,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if remux.returncode != 0:
            raise ConnectionError(f"FFmpeg remux failed for {fname}")"""

new_ffmpeg = """        remux = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            tmp_fname,
            "-c:a",
            "copy",
            "-f",
            "flac",
            fname,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await remux.communicate()
        if remux.returncode != 0:
            raise ConnectionError(f"FFmpeg remux failed for {fname}")"""

content = content.replace(old_ffmpeg, new_ffmpeg)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
