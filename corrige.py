import re, glob

for filepath in glob.glob('qobuz_dl/**/*.py', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Protege os blocos grandes (HTML, SVG, Docstrings em aspas triplas)
    placeholders = {}

    def save_triple(m, placeholders=placeholders):
        k = f'__TRIPLE_{len(placeholders)}__'
        placeholders[k] = m.group(0)
        return k

    # Esconde temporariamente as aspas triplas
    tc = re.sub(
        r'([fF]?"""[\s\S]*?"""|[fF]?\'\'\'[\s\S]*?\'\'\')',
        save_triple,
        content,
    )

    # 2. Logica cirurgica: so tira o enter de dentro das chaves { }
    def fix_fstring(m):
        s = m.group(0)
        res = []
        in_brace = 0
        for c in s:
            if c == '{':
                in_brace += 1
                res.append(c)
            elif c == '}':
                in_brace = max(0, in_brace - 1)
                res.append(c)
            elif c in '\r\n' and in_brace > 0:
                res.append(' ')  # Troca o Enter por espaco
            else:
                res.append(c)
        return ''.join(res)

    # Busca apenas f-strings normais (f"..." ou f'...')
    tc = re.sub(
        r'[fF]"(?:[^"\\]|\\[\s\S])*"|[fF]\'(?:[^\'\\]|\\[\s\S])*\'',
        fix_fstring,
        tc,
    )

    # 3. Devolve os blocos protegidos pro lugar original
    for k, v in placeholders.items():
        tc = tc.replace(k, v)

    # Salva apenas se algo foi corrigido
    if tc != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(tc)
        print(f'Corrigido de forma segura: {filepath}')
