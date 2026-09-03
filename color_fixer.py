import re

file_path = "qobuz_dl/downloader.py"  # Ajuste se estiver em subpasta

with open(file_path, "r", encoding="utf-8") as f:
  lines = f.readlines()

new_lines = []
total = 0

for line in lines:
  # Procura chamadas ui.warn, ui.error ou ui.skip que ainda não contenham {YELLOW}, {RED} ou {OFF}
  if any(fn in line for fn in ["ui.warn(", "ui.error(", "ui.skip("]) and not any(
      c in line for c in ["{YELLOW}", "{RED}", "{OFF}", "{RESET}"]
  ):

    # Descobre qual é a função e substitui para f-string injetando a cor correspondente
    if "ui.warn(" in line:
      color_start, color_end = "{YELLOW}", "{OFF}"
      line = line.replace("ui.warn(", f'ui.warn(f"{color_start}')
    elif "ui.skip(" in line:
      color_start, color_end = "{YELLOW}", "{OFF}"
      line = line.replace("ui.skip(", f'ui.skip(f"{color_start}')
    elif "ui.error(" in line:
      color_start, color_end = "{RED}", "{OFF}"
      line = line.replace("ui.error(", f'ui.error(f"{color_start}')

    # Fecha a f-string antes do último parêntese da função
    if line.strip().endswith(")"):
      # Insere o fechamento da cor logo antes do parêntese final
      line = line.rstrip()
      # Remove o último parêntese para injetar o color_end
      if line.endswith(")"):
        line = line[:-1] + f"{color_end})"

    total += 1

  new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
  f.writelines(new_lines)

print(f"Varredura concluída! {total} linhas ajustadas.")

