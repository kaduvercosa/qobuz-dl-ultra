with open("qobuz_dl/downloader.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "import asyncio" in line:
        new_lines.append(line)
        new_lines.append("import aiohttp\nimport aiofiles\n")
    elif "self.http_session = requests.Session()" in line:
        new_lines.append("        self.http_session = None\n")
    elif "self.http_session.headers.update(" in line:
        new_lines.append("        # self.http_session headers will be updated during aiohttp initialization\n")
    elif "\"User-Agent\": \"Mozilla/5.0" in line and "self.http_session" not in "".join(lines):
        pass # Handle this below instead
    else:
        new_lines.append(line)

with open("qobuz_dl/downloader.py", "w") as f:
    f.writelines(new_lines)
