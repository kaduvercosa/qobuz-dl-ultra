import re

with open("qobuz_dl/qopy.py", "r") as f:
    content = f.read()

# Replace aiohttp imports
content = content.replace("import aiohttp", "import httpx")

# Replace aiohttp.ClientSession with httpx.AsyncClient
content = re.sub(
    r"client_timeout = aiohttp\.ClientTimeout\([\s\S]*?\)\s*self\.session = aiohttp\.ClientSession\(headers=headers, timeout=client_timeout\)",
    r"client_timeout = httpx.Timeout(15.0, read=90.0, connect=15.0)\n        limits = httpx.Limits(max_keepalive_connections=200, max_connections=200)\n        self.session = httpx.AsyncClient(headers=headers, timeout=client_timeout, http2=True, limits=limits)",
    content
)

# Update self.session.close() to self.session.aclose()
content = content.replace("await self.session.close()", "await self.session.aclose()")

# Update async with self.session.request(...)
content = re.sub(
    r"async with self\.session\.request\(\n\s*method, self\.base \+ epoint, \*\*req_kwargs\n\s*\) as r:",
    r"r = await self.session.request(method, self.base + epoint, **req_kwargs)",
    content
)

# Update r.status to r.status_code
content = content.replace("r.status ==", "r.status_code ==")
content = content.replace("r.status", "r.status_code")

# Update r.text() to r.text
content = content.replace("await r.text()", "r.text")
# Update r.json() to r.json() (no await needed for httpx)
content = content.replace("await r.json()", "r.json()")

# Update exceptions
content = content.replace("aiohttp.ClientError", "httpx.RequestError")

# Handle asyncio.sleep delay inside api_call loop
# We need to make sure the indentation is correct for the request call since it's no longer an async with block
# Let's use a simpler regex for the request part
content = content.replace("async with self.session.request(\n                    method, self.base + epoint, **req_kwargs\n                ) as r:\n", "r = await self.session.request(method, self.base + epoint, **req_kwargs)\n                if True:\n")
content = content.replace("async with self.session.request(\n                    method, self.base + epoint, **req_kwargs\n                ) as r:", "r = await self.session.request(method, self.base + epoint, **req_kwargs)\n                if True:")

# Fix the lyricsUrl request in _fetch_qobuz_lyrics_json (actually this is in downloader.py? Oh, there is one in qopy.py? Wait, I saw it in downloader.py, but qopy doesn't have it except in `get_track_lyrics_url`). Oh wait, the `async with self.client.session.request("get"...` is in downloader.py. Wait, no, qopy.py might not have it. Let's check `api_call`.

with open("qobuz_dl/qopy.py", "w") as f:
    f.write(content)
