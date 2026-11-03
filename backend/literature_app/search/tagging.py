from __future__ import annotations
from enum import StrEnum

class H2O2Status(StrEnum):
    YES = "YES"
    NO = "NO"
    UNCERTAIN = "UNCERTAIN"

CELL_TERMS = ("AC16", "HCM", "primary human cardiomyocyte", "NRCM", "H9c2", "HL-1", "VSMC", "vascular smooth muscle cell", "neonatal rat cardiomyocyte")

def tag_h2o2(text: str | None) -> H2O2Status:
    if not text:
        return H2O2Status.UNCERTAIN
    lowered = text.casefold()
    if any(term.casefold() in lowered for term in ("hydrogen peroxide", "h2o2", "h₂o₂")):
        return H2O2Status.YES
    return H2O2Status.NO

def tag_cell_models(text: str | None) -> tuple[str, ...]:
    if not text:
        return ("unknown",)
    lowered = text.casefold()
    found = tuple(term for term in CELL_TERMS if term.casefold() in lowered)
    return found or ("unknown",)
