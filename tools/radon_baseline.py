import json
import pathlib
import sys

from radon.complexity import cc_visit
from radon.visitors import Function

BASE = pathlib.Path("radon-baseline.json")
LIMITE = 10


def medir() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in sorted(pathlib.Path("qobuz_dl").rglob("*.py")):
        from radon.visitors import Function

        for b in cc_visit(p.read_text(encoding="utf-8")):
            if isinstance(b, Function) and b.complexity > LIMITE:
                out[f"{p.as_posix()}::{b.fullname}"] = b.complexity
    return out


atual = medir()
if "--gerar" in sys.argv:
    BASE.write_text(json.dumps(atual, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Baseline gerado: {len(atual)} funções acima de {LIMITE}.")
    sys.exit(0)

base = json.loads(BASE.read_text(encoding="utf-8"))
piorou = {
    k: (base.get(k, LIMITE), v) for k, v in atual.items() if v > base.get(k, LIMITE)
}
for k, (antes, agora) in sorted(piorou.items()):
    print(f"PIOROU {k}: {antes} -> {agora}")
sys.exit(1 if piorou else 0)
