"""Loader for the prompt files in app/prompts.

File layout: a "# System" section and a "# User" section. Every literal brace in the file is
doubled ("{{" / "}}"). The System text is returned ready to send (braces unescaped). The User
text is returned as a str.format template: the caller fills the named fields, for example
`user.format(page_no=3, page_count=188, filename="record_1.pdf")`.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SECTION = re.compile(r"^#\s*(System|User)\s*$", re.MULTILINE | re.IGNORECASE)


@lru_cache(maxsize=None)
def load_prompt(name: str) -> tuple[str, str]:
    """Return (system, user_template) for app/prompts/<name>.md."""
    path = PROMPTS_DIR / (name if name.endswith(".md") else f"{name}.md")
    text = path.read_text(encoding="utf-8")
    parts = _SECTION.split(text)
    # parts: [preamble, "System", body, "User", body] in file order.
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].lower()] = parts[i + 1].strip()
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"{path.name}: needs a '# System' and a '# User' section")
    system = sections["system"].replace("{{", "{").replace("}}", "}")
    return system, sections["user"]
