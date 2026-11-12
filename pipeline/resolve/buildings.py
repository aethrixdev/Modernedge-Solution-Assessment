"""One record per building, named exactly as in Schedule B.

Every source spells buildings differently ("HALCYON TWR", "S-101",
"Halcyon Twr", "Meridian One Bldg", "BLD-01", department "410", "Halcyon").
We match names by their words after expanding common abbreviations
(TWR -> TOWER, PLZ -> PLAZA, 1 -> ONE ...). Codes that carry no name at all
(HR's BLD-xx, ledger departments) are mapped from evidence in the data:
which building the people at that code actually punch in at.

Every mapping is recorded with its method and confidence, so it can be shown
in the decisions log and checked.
"""
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from ..common import Findings, norm

# Generic English abbreviations, not names from this dataset.
ABBREVIATIONS = {
    "TWR": "TOWER", "PLZ": "PLAZA", "HSE": "HOUSE", "ANX": "ANNEX", "BLDG": "BUILDING",
    "BLD": "BUILDING", "CTR": "CENTER", "CNTR": "CENTER", "STE": "SUITE", "ST": "STREET",
    "AVE": "AVENUE", "SQ": "SQUARE", "PK": "PARK",
    "1": "ONE", "2": "TWO", "3": "THREE", "4": "FOUR", "5": "FIVE", "6": "SIX", "7": "SEVEN", "8": "EIGHT", "9": "NINE",
}
NOISE = {"BUILDING", "THE", "AT", "BLDG"}


def name_tokens(text: str) -> frozenset[str]:
    """The words of a name after clean-up and expanding abbreviations: 'HALCYON TWR' -> {HALCYON, TOWER}."""
    words = [ABBREVIATIONS.get(w, w) for w in norm(text).split()]
    return frozenset(w for w in words if w not in NOISE)


def _same_word(a: str, b: str) -> bool:
    """Same word, allowing a small typo in words longer than 3 letters."""
    return a == b or (len(a) > 3 and len(b) > 3 and SequenceMatcher(None, a, b).ratio() >= 0.85)


def _covered(small: frozenset, big: frozenset) -> bool:
    """True if every word in `small` appears (allowing typos) in `big`."""
    return all(any(_same_word(s, b) for b in big) for s in small)


@dataclass
class Alias:
    """One source's name or code for a building, with how it was matched and how sure we are."""
    source: str
    text: str
    building: str | None
    method: str
    confidence: float


class BuildingRegistry:
    """The official building list (Schedule B names) plus every alias mapped to it."""
    def __init__(self, canonical: list[str]):
        """Start from the Schedule B names; no aliases yet."""
        self.canonical = list(dict.fromkeys(canonical))   # Schedule B names, in order
        self._tokens = {c: name_tokens(c) for c in self.canonical}
        self.aliases: dict[tuple[str, str], Alias] = {}

    def match_name(self, text: str) -> tuple[str | None, float, str]:
        """Return (building, confidence, method) for a free-text building name."""
        t = name_tokens(text)
        if not t:
            return None, 0.0, "empty"
        exact = [c for c, ct in self._tokens.items() if ct == t]
        if len(exact) == 1:
            return exact[0], 1.0, "same words"
        fuzzy = [c for c, ct in self._tokens.items() if _covered(t, ct) and _covered(ct, t)]
        if len(fuzzy) == 1:
            return fuzzy[0], 0.95, "same words (spelling variant)"
        # "Halcyon" -> "Halcyon Tower": every word of the text is in exactly one building
        within = [c for c, ct in self._tokens.items() if _covered(t, ct)]
        if len(within) == 1:
            return within[0], 0.85, "partial name, unique"
        # "Meridian One Bldg (OLD - DO NOT USE)": a building's words all appear in the text
        contains = [c for c, ct in self._tokens.items() if _covered(ct, t)]
        if len(contains) == 1:
            return contains[0], 0.75, "text contains building name"
        if len(within) > 1 or len(contains) > 1:
            return None, 0.0, f"ambiguous between {sorted(set(within) | set(contains))}"
        return None, 0.0, "no match"

    def register(self, source: str, text: str, findings: Findings | None = None,
                 building: str | None = None, method: str | None = None, confidence: float | None = None) -> str | None:
        """Map one source's name/code to a building and remember the mapping."""
        key = (source, text)
        if key in self.aliases:
            return self.aliases[key].building
        if building is None and method is None:
            building, confidence, method = self.match_name(text)
        self.aliases[key] = Alias(source, text, building, method, confidence or 0.0)
        if building is None and findings is not None:
            findings.exception(source, text, f"building not resolved ({method})", "records at this site left unattributed", 0.0)
        return building

    def lookup(self, source: str, text: str) -> str | None:
        """The building an already-registered name/code maps to (or None)."""
        a = self.aliases.get((source, text))
        return a.building if a else None

    def register_by_evidence(self, source: str, code: str, votes: Counter, findings: Findings, what: str) -> str | None:
        """Map a code with no name (BLD-03, dept 430) to the building most of
        its evidence points at, e.g. where its employees punch in."""
        if not votes:
            findings.exception(source, code, f"no evidence to map {what} to a building", "left unmapped", 0.0)
            return self.register(source, code, None, building=None, method="no evidence", confidence=0.0)
        building, n = votes.most_common(1)[0]
        share = n / sum(votes.values())
        if share < 0.6:
            findings.exception(source, code, f"{what} evidence is split: {dict(votes)}", f"mapped to {building}", share)
        return self.register(source, code, building=building,
                             method=f"{what}: {n} of {sum(votes.values())} records point here", confidence=round(share, 2))
