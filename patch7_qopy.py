with open("qobuz_dl/qopy.py", "r") as f:
    lines = f.readlines()

new_lines = []
for idx, line in enumerate(lines):
    if "                    )" in line and "f\"{YELLOW}[!] Skipping:" in lines[idx-1]:
        new_lines.append("                        )\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/qopy.py", "w") as f:
    f.writelines(new_lines)
