with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

content = content.replace(
    '    async def download_id_by_type(\n        self, track=True, is_parallel=False, position_pool=None\n    ):',
    '    async def download_id_by_type(\n        self, track=True, is_parallel=False, position_pool=None\n    ):\n        self.http_session = aiohttp.ClientSession(\n            connector=aiohttp.TCPConnector(limit=200),\n            headers={\n                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",\n                "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",\n                "Connection": "keep-alive",\n            }\n        )'
)

# close_session needs to be async because aiohttp.ClientSession.close is an async function
content = content.replace(
    '    def close_session(self):',
    '    async def close_session(self):'
)

content = content.replace(
    '            self.close_session()',
    '            await self.close_session()'
)

content = content.replace(
    '                session.close()',
    '                await session.close()'
)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
