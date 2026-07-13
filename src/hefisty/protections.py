"""Protecciones de entrada y salida.

Entrada: detección de prompt injection por reglas — aplicada al mensaje del usuario y,
sobre todo, a los chunks que vuelven del retrieval (documentos de terceros pueden traer
instrucciones maliciosas: se marcan y degradan, no se inyectan tal cual).
Salida: escaneo por patrones de credenciales; los fragmentos detectados se redactan.
"""

from __future__ import annotations

import re

# --- Entrada: prompt injection ---

_INJECTION_PATTERNS = [
    re.compile(r"ignora(r|s)?\s+(todas?\s+)?(tus|las)\s+instrucciones", re.I),
    re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?(previous|above|prior)", re.I),
    re.compile(r"olvida(r|te)?\s+(todo\s+)?lo\s+anterior", re.I),
    re.compile(r"(you\s+are\s+now|ahora\s+eres)\b", re.I),
    re.compile(r"reveal\s+(your\s+)?(system|instructions|prompt)", re.I),
    re.compile(r"(muestra|revela)\s+(tu\s+)?(prompt|instrucciones)\s+de\s+sistema", re.I),
    re.compile(r"</?(system|assistant|user)>", re.I),
    re.compile(r"\[/?INST\]", re.I),
    re.compile(r"###\s*(system|instrucciones)", re.I),
]


def detect_injection(text: str) -> list[str]:
    """Devuelve las descripciones de los patrones de injection que casan."""
    return [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]


def sanitize_chunk(text: str) -> tuple[str, bool]:
    """Degrada un chunk si contiene posibles instrucciones incrustadas.

    Devuelve (texto_seguro, degradado). El texto degradado NO reinyecta instrucciones.
    """
    if detect_injection(text):
        return "[fragmento omitido: contenía posibles instrucciones incrustadas]", True
    return text, False


# --- Salida: redacción de credenciales ---

_CREDENTIAL_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b", re.I),
]

_REDACTED = "[REDACTADO]"


def redact_credentials(text: str) -> tuple[str, int]:
    """Reemplaza credenciales con formato conocido por `[REDACTADO]`.

    Devuelve (texto_redactado, número_de_redacciones).
    """
    count = 0

    def _sub(_m: re.Match) -> str:
        nonlocal count
        count += 1
        return _REDACTED

    for pat in _CREDENTIAL_PATTERNS:
        text = pat.sub(_sub, text)
    return text, count
