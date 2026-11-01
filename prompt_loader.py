"""Read editable model prompts afresh for each request."""
from pathlib import Path

PROMPT_FILE = Path(__file__).with_name("prompts.txt")


def load_prompt(name: str) -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    start, end = f"@@ {name} @@\n", "\n@@ END @@"
    if text.count(start) != 1:
        raise ValueError(f"Prompt section missing or duplicated: {name}")
    section = text.split(start, 1)[1].split(end, 1)
    if len(section) != 2 or not section[0].strip():
        raise ValueError(f"Prompt section empty or unterminated: {name}")
    return section[0]
