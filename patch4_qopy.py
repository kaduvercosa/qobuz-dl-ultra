with open("qobuz_dl/qopy.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("                    elif ("):
        new_lines.append("                elif (\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/qopy.py", "w") as f:
    f.writelines(new_lines)
