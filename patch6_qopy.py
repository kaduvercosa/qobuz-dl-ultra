with open("qobuz_dl/qopy.py", "r") as f:
    lines = f.readlines()

new_lines = []
for idx, line in enumerate(lines):
    # Fix the `else` inside `elif highest_ratio >= PROMPT_THRESHOLD`
    if "else:" in line and "print(f\"{RED}    [-] Track skipped manually.{OFF}\")" in lines[idx+1]:
        new_lines.append("                        else:\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/qopy.py", "w") as f:
    f.writelines(new_lines)
