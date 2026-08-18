with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

content = content.replace('r = await self.client.session.request(\n                "get", self.client.base + "track/lyricsUrl", params=params\n            ) as r:', 'r = await self.client.session.request(\n                "get", self.client.base + "track/lyricsUrl", params=params\n            )')
content = content.replace('if r.status != 200:', 'if r.status_code != 200:')
content = content.replace('lyrics_url_meta = await r.json()', 'lyrics_url_meta = r.json()')

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
