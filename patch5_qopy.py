with open("qobuz_dl/qopy.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("                        and r.status_code == 400"):
        new_lines.append("                    and r.status_code == 400\n")
    elif line.startswith("                    ):"):
        new_lines.append("                ):\n")
    elif line.startswith("                        body = r.json()"):
        new_lines.append("                    body = r.json()\n")
    elif line.startswith("                        raise InvalidAppSecretError("):
        new_lines.append("                    raise InvalidAppSecretError(\n")
    elif line.startswith("                            f\"Invalid app secret: {body}.\\n\" + RESET"):
        new_lines.append("                        f\"Invalid app secret: {body}.\\n\" + RESET\n")
    elif line.startswith("                        )"):
        new_lines.append("                    )\n")
    elif line.startswith("                    if epoint == \"user/get\" and r.status_code == 400:"):
        new_lines.append("                if epoint == \"user/get\" and r.status_code == 400:\n")
    elif line.startswith("                        return {}"):
        new_lines.append("                    return {}\n")
    elif line.startswith("                    r.raise_for_status()"):
        new_lines.append("                r.raise_for_status()\n")
    elif line.startswith("                    data = r.json()"):
        new_lines.append("                data = r.json()\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/qopy.py", "w") as f:
    f.writelines(new_lines)
