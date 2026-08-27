"""Detección de lenguaje por palabras clave, para elegir el diccionario RAG.

Solo se listan lenguajes que YA tienen colección/diccionario: añadir palabras de un
lenguaje sin colección haría consultar una colección inexistente. El comodín
`patrones` (cross-lenguaje) se consulta siempre, aparte de esta detección.
"""

from __future__ import annotations

_LANG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kotlin": (
        "kotlin",
        "android",
        "compose",
        "jetpack",
        "gradle",
        "coroutine",
        "corrutina",
        "room",
        "hilt",
        "viewmodel",
        ".kt",
        "livedata",
    ),
}


def detect_language(text: str) -> str | None:
    low = text.lower()
    for lang, kws in _LANG_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return lang
    return None


def collections_for(lang: str | None, extra: list[str] | None = None) -> list[str]:
    """Colecciones a consultar en retrieval: lenguaje detectado + extras (config de
    proyecto) + comodín `patrones`. Sin vacíos ni duplicados, preservando el orden."""
    out: list[str] = []
    for c in [lang, *(extra or []), "patrones"]:
        if c and c not in out:
            out.append(c)
    return out
