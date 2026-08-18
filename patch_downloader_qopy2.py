with open("qobuz_dl/downloader.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("                if r.status_code != 200:"):
        new_lines.append("            if r.status_code != 200:\n")
    elif line.startswith("                    return None"):
        new_lines.append("                return None\n")
    elif line.startswith("                lyrics_url_meta = r.json()"):
        new_lines.append("            lyrics_url_meta = r.json()\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/downloader.py", "w") as f:
    f.writelines(new_lines)
