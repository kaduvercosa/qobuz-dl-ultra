with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

old_lyrics_fetch = """            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: self.http_session.get(lyrics_json_url, timeout=12),
            )

            if resp.status_code in (403, 404):
                return None

            resp.raise_for_status()
            return resp.json()"""

new_lyrics_fetch = """            resp = await self.http_session.get(lyrics_json_url, timeout=aiohttp.ClientTimeout(total=12))

            if resp.status in (403, 404):
                return None

            resp.raise_for_status()
            return await resp.json()"""

content = content.replace(old_lyrics_fetch, new_lyrics_fetch)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
