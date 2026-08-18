with open("qobuz_dl/downloader.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "self.http_session = None" in line:
        new_lines.append(line)
        skip = True
    elif skip and "self.fetch_lyrics = fetch_lyrics" in line:
        skip = False
        new_lines.append(line)
    elif not skip:
        new_lines.append(line)

with open("qobuz_dl/downloader.py", "w") as f:
    f.writelines(new_lines)
