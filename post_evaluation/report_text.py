"""Presentation-only cleanup; evidence IDs remain in structured draft metadata."""
import re


def narrative_text(text: str) -> str:
    cluster = r"P\d+D\d+C\d+(?:\s*[|,，;；、]\s*P\d+D\d+C\d+)*"
    text = re.sub(rf"\[\s*{cluster}\s*\]|【\s*{cluster}\s*】|\(\s*{cluster}\s*\)|（\s*{cluster}\s*）|{cluster}", "", text)
    return re.sub(r"[ \t]+([，。；：、！？])", r"\1", text).strip()
