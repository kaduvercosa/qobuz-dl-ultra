with open("qobuz_dl/qopy.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("                        text = r.text"):
        new_lines.append("                    text = r.text\n")
    elif line.startswith("                        if \"invalid\" in text.lower():"):
        new_lines.append("                    if \"invalid\" in text.lower():\n")
    elif line.startswith("                            raise AuthenticationError(\"Invalid email or password.\")"):
        new_lines.append("                        raise AuthenticationError(\"Invalid email or password.\")\n")
    elif line.startswith("                        else:"):
        new_lines.append("                    else:\n")
    elif line.startswith("                            logger.info(f\"{GREEN}Logged: OK{OFF}\")"):
        new_lines.append("                        logger.info(f\"{GREEN}Logged: OK{OFF}\")\n")
    else:
        new_lines.append(line)

with open("qobuz_dl/qopy.py", "w") as f:
    f.writelines(new_lines)
