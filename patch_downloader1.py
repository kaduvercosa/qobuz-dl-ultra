import re

with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

# Add aiohttp and aiofiles imports
content = content.replace("import asyncio", "import asyncio\nimport aiohttp\nimport aiofiles")

# Replace requests.Session() with aiohttp.ClientSession in __init__
# We need to create it properly since __init__ is synchronous, maybe we should create it inside an async method or when needed?
# Actually, the downloader uses self.http_session. We can make it an aiohttp.ClientSession where needed or initialize it async.
# The user wants "Add `aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=200))` directly in the downloader."
# Wait, aiohttp.ClientSession must be created inside an async function or with a running event loop.
# `Download.download_id_by_type` is an async function. We can initialize it there!
