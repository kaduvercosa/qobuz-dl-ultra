from pathlib import Path

FILES = (
    Path("tests/unit/test_core_interactive_branches.py"),
    Path("tests/unit/test_core_interactive_extra.py"),
    Path("tests/unit/test_core_interactive_paths.py"),
)

for path in FILES:
    text = path.read_text()
    updated = text.replace(
        "raise KeyboardInterrupt\n", "raise KeyboardInterrupt from None\n"
    )
    if updated != text:
        path.write_text(updated)
