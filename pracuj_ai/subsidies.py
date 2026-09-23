"""Companies that received public funding (PARP / NCBR / UE) — a hiring signal.

Why this matters: firms that just won a grant (Ścieżka SMART, FENG, NCBR B+R)
typically scale teams and post new roles. Cross-referencing the published
beneficiary lists with live pracuj.pl offers surfaces employers that are both
relevant AND actively expanding.

The seed list below is curated from publicly published PARP/NCBR beneficiary
lists (verifiable) plus well-documented Polish tech firms with EU/PARP support.
Extend it with ``--companies file.json`` — a JSON list of objects
``{"name":..., "note":..., "search":...}`` where ``search`` is the single brand
token pracuj.pl matches on (multi-word names break the SPA search, so we use the
distinctive brand word). The user owns the truth of who was funded.

Searching is read-only: we open ``it.pracuj.pl`` and match the brand token
against live offers (same browser substrate as the rest of pracuj-ai). Boilerplate
"suggested" offers (returned when a brand has no live postings) are filtered out
in ``browser._parse_offers``.
"""
from __future__ import annotations

import json
from pathlib import Path

from .browser import search_offers

# name = display, note = program/domain (audit trail), search = brand token for pracuj.
SEEDED: list[dict] = [
    # --- verifiable from published PARP/NCBR beneficiary lists ---
    {"name": "LLM GenFramework AI Investments", "note": "NCBR B+R; AI/LLM asystent inwestycyjny; 2,39 mln zł", "search": "GenFramework"},
    {"name": "IT MINE", "note": "PARP Ścieżka SMART; IT / Visio", "search": "IT MINE"},
    {"name": "Centrum Słuchu i Mowy", "note": "PARP; platforma AI do terapii CAPD", "search": "Centrum Słuchu i Mowy"},
    {"name": "PHSP Medical Simulation Technologies", "note": "NCBR; symulacje medyczne (soft)", "search": "PHSP"},
    {"name": "Bright", "note": "NCBR; (branża do ustalenia)", "search": "Bright"},
    {"name": "MEWO", "note": "PARP Ścieżka SMART", "search": "MEWO"},
    {"name": "JARPAK", "note": "PARP Ścieżka SMART; opakowania (nie-IT)", "search": "JARPAK"},
    # --- well-documented Polish tech firms with EU/PARP/PFR support ---
    {"name": "Infermedica", "note": "scaleup/UE; health-tech AI", "search": "Infermedica"},
    {"name": "VoiceLab", "note": "UE; speech / voice AI", "search": "VoiceLab"},
    {"name": "Tidio", "note": "UE/PFR; customer-service AI", "search": "Tidio"},
    {"name": "Survicate", "note": "UE; SaaS ankiety", "search": "Survicate"},
    {"name": "Estimote", "note": "UE; IoT / beacons", "search": "Estimote"},
    {"name": "Brainly", "note": "UE/PFR; edtech", "search": "Brainly"},
    {"name": "Docplanner", "note": "UE/PFR; health-tech (ZnanyLekarz)", "search": "Docplanner"},
    {"name": "Intive", "note": "IT; (sprawdź)", "search": "Intive"},
    {"name": "STX Next", "note": "IT; (sprawdź)", "search": "STX Next"},
    {"name": "Netguru", "note": "IT; (sprawdź)", "search": "Netguru"},
    # --- dodatkowi realni beneficjenci PARP/NCBR (IT/AI) z jawnych list ocenionych projektów ---
    {"name": "KP Labs", "note": "Ścieżka SMART; AI dla przemysłu kosmicznego (~6,4 mln zł)", "search": "KP Labs"},
    {"name": "Saventic Health", "note": "Ścieżka SMART; silnik AI wspierający (health)", "search": "Saventic"},
    {"name": "AICHEMIST (Mad Moth)", "note": "Ścieżka SMART; generatywna AI (Text-to-AV) produkcja wideo", "search": "AICHEMIST"},
    {"name": "Notoria ESG", "note": "Ścieżka SMART; automatyczne ratingi ESG oparte na LLM", "search": "Notoria"},
    {"name": "Biocam", "note": "NCBR B+R; kapsułka endoskopowa + AI (medtech)", "search": "Biocam"},
    {"name": "IDEAS NCBR", "note": "NCBR; instytut R&D AI (platforma akademia+biznes)", "search": "IDEAS NCBR"},
    # --- świeżo zidentyfikowani beneficjenci PARP/FENG (IT/AI, sygnał zatrudnienia) ---
    {"name": "Trusted Software Services (TSS)", "note": "PARP/FENG ~5,0 mln zł; automatyzacja screeningu rekrutacyjnego dla branży IT (Ścieżka SMART)", "search": "Trusted Software Services"},
    {"name": "WeegreeAI (Weegree Europe)", "note": "FENG 8,98 mln zł; system LLM do wspierania procesów rekrutacyjnych (B+R + Cyfryzacja 2024-2028)", "search": "Weegree"},
    {"name": "Galaxy Systemy Informatyczne", "note": "FENG 05.01 (Fundusz Wsparcia Technologii Krytycznych) 35,9 mln zł; Quantum AI Cloud", "search": "Galaxy Systemy Informatyczne"},
    {"name": "ALVENTA S.A.", "note": "FENG 05.01 18,8 mln zł; wzmocnienie niezależności UE / technologie cyfrowe", "search": "ALVENTA"},
    {"name": "MONDI Sp. z o.o.", "note": "PARP 2,48 mln zł; system AI wspierający rekrutację i monitorowanie pracy", "search": "MONDI"},
]


def load_companies(path: str | None = None) -> list[dict]:
    """Return company dicts. Load from JSON if ``path`` given, else seed list."""
    if not path:
        return [dict(c) for c in SEEDED]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[dict] = []
    for c in data:
        if isinstance(c, str):
            out.append({"name": c, "note": "", "search": c})
        else:
            out.append({
                "name": str(c.get("name", c.get("search", ""))),
                "note": str(c.get("note", "")),
                "search": str(c.get("search", c.get("name", ""))),
            })
    return [o for o in out if o["name"] or o["search"]]


def find_offers(
    company: dict, city: str | None = None, remote: bool = False, limit: int = 10
) -> list[dict]:
    """Live pracuj.pl offers for a company's brand token (read-only browser search)."""
    return search_offers(company.get("search", company["name"]), city=city, limit=limit, remote=remote)


def scan(
    companies: list[dict],
    city: str | None = None,
    remote: bool = False,
    limit: int = 10,
) -> list[dict]:
    """For each company, fetch its current offers. Returns per-company result rows."""
    rows: list[dict] = []
    for c in companies:
        try:
            offers = find_offers(c, city=city, remote=remote, limit=limit)
        except Exception:  # noqa: BLE001 - one bad company shouldn't abort the scan
            offers = []
        rows.append({"company": c["name"], "note": c.get("note", ""), "offers": offers})
    return rows
