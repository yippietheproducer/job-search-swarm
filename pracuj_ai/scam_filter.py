"""Deterministyczny filtr scamów — działa PRZED (i niezależnie od) oceny LLM.

Źródło listy: research/OFFER_SCAM_CHECK.md (werdykty 2026-08-23) + audyt z
2026-09-22 (lane 6 + ręczna weryfikacja orkiestratora, patrz
research/remote_parttime/SOURCE_RELIABILITY.md).

Zasada: podejrzana domena = oferta NIE trafia na dashboard, tylko do tabeli z
flagą `scam_suspect=1` (człowiek decyduje po ręcznej weryfikacji).

## Dlaczego rozszerzono listę (2026-09-22)

Audyt bazy wykazał, że **najwyżej ocenione oferty (fit 92–95) to w większości
śmieci z farm reklamowych**, a prawdziwi pracodawcy (EY, Dynatrace, SD Worx)
siedzą NIŻEJ (88). Mechanizm:

1. Farmy rejestrują subdomeny "firm" na **darmowych hostingach** (`zya.me`,
   `10001mb.com`, `is-great.net`, `infinityfree.me`, `42web.io`, `unaux.com`,
   `totalh.net`) — sprawdzone: `flexgen.zya.me`, `workflowza.zya.me`,
   `worksynergy.10001mb.com`, `vacancies.is-great.net` zwracają niemal identyczną
   powłokę JS ~850–890 B („This site requires Javascript to work").
2. Ogłoszenie to **szablon ~275–330 znaków** bez produktu, zespołu i wymagań,
   np. HireSub: „Flexible title, time, and commitment (full-time, part-time,
   side-gig, or contract). No degree required. Early-career developers welcome."
   — czyli tekst nasycony słowami kluczowymi, który pasuje do KAŻDEGO filtra
   (również „remote + part-time"), więc scoring windowany na słowach wynosi go
   na szczyt.

Filtr działa więc na trzech warstwach: domena, tytuł i tekst ogłoszenia.

## Platformowa sygnatura farmy (dowód z lane 8, 2026-09-22)

`zya.me`, `is-great.net`, `10001mb.com` i `2kool4u.net` serwują **bajtowo identyczny
`aes.js` slowAES challenge** (stałe `a=f655ba9d…`, `b=98344c2e…`, `c=8b4f7c1a…`) na
KAŻDĄ ścieżkę, także `/`. Lane 8 rozwiązał ten challenge w Node i pobrał stronę
ponownie z poprawnym cookie (`__test=…`) — **strona nadal zwraca tylko challenge**.
Czyli za linkiem nie ma żadnego ogłoszenia.

Uczciwe zastrzeżenie: identyczne stałe dowodzą **tej samej platformy hostingowej**, a
niekoniecznie tego samego operatora. To sygnatura farmy lead-harvesting, nie dowód
konkretnego podmiotu.
"""
from __future__ import annotations

import re

# --- warstwa 1: domena -------------------------------------------------------
# Zweryfikowane farmy (subdomeny "firm" na darmowym hostingu).
SCAM_DOMAIN_PATTERNS = (
    "zya.me",            # flexgen.zya.me, workflowza.zya.me — 872 B shell
    "infinityfree.",     # hiresub.infinityfree.me — lead-harvesting
    "10001mb.com",       # worksynergy.10001mb.com — 888 B shell
    "is-great.net",      # vacancies.is-great.net — 849 B shell
    "42web.io",          # jquasar.42web.io (farm, lane 6)
    "unaux.com",         # brighthush.8r.unaux.com, remote.mq.unaux.com
    "totalh.net",        # joblume/homebased/taskworks/prairie.totalh.net
    "66ghz.com",         # talentpulse.66ghz.com
    "liveblog365.com",   # lynqo.liveblog365.com
    "hiresub.",
    "pragatishilclasses.org",
    "mysmartpros.com",
    "2kool4u.net",       # remoteforge.2kool4u.net — same platform signature (lane 8)
)

# Ogólny wzorzec „darmowa subdomena" — łapie NOWE farmy bez czekania na audyt.
# Uwaga: celowo wąski (znane providery free-hostingu), by nie flagować
# prawdziwych stron firmowych.
FREE_HOST_RE = re.compile(
    r"(?:^|//|\.)(?:[a-z0-9-]+\.)?"
    r"(?:zya\.me|10001mb\.com|is-great\.net|infinityfree\.(?:com|me)|"
    r"42web\.io|unaux\.com|totalh\.net|66ghz\.com|liveblog365\.com|2kool4u\.net|"
    r"000webhostapp\.com|infinityfreeapp\.com|epizy\.com|rf\.gd|"
    r"freehostia\.com|byethost\.com|smartyacad\.com|weebly\.com|wixsite\.com)"
    r"(?::\d+)?(?:/|$)",
    re.I,
)

# --- warstwa 2: tytuł --------------------------------------------------------
SCAM_TITLE_PATTERNS = (
    "no degree required",   # bait — razem z darmową domeną
    "no experience needed",
)

# --- warstwa 3: tekst ogłoszenia (szablon farmy) -----------------------------
# Frazy, które w audycie wystąpiły w szablonowych ogłoszeniach farm i praktycznie
# nie występują w prawdziwych ogłoszeniach razem z darmową domeną.
SCAM_TEXT_PATTERNS = (
    "flexible title, time, and commitment",
    "side-gig, or contract",
    "no degree required",
    "early-career developers welcome",
)

# Progi jakości ogłoszenia: ogłoszenie <=300 znaków bez ani jednego konkretu
# technicznego jest zbyt cienkie, by cokolwiek o nim wnioskować.
THIN_TEXT_MAX = 300


def _has_tech_signal(text: str) -> bool:
    """Czy ogłoszenie zawiera JAKIKOLWIEK konkret techniczny?"""
    return bool(re.search(
        r"\b(react|next\.?js|typescript|javascript|python|node|django|flask|"
        r"java\b|go\b|rust|php|laravel|vue|svelte|sql|postgres|mysql|mongodb|"
        r"redis|aws|gcp|azure|docker|kubernetes|terraform|graphql|rest|api|"
        r"git|ci/?cd|tailwind|html|css|api)\b",
        text or "", re.I))


def is_suspect(url: str | None, title: str | None = "", text: str | None = None) -> bool:
    """True = oferta trafia do kwarantanny (`scam_suspect=1`), nie na dashboard.

    `text` jest opcjonalny (zgodność wsteczna): bez niego działają warstwy
    domenowa i tytułowa, tak jak przed 2026-09-22.
    """
    u = (url or "").lower()
    t = (title or "").lower()
    body = (text or "").lower()

    if any(p in u for p in SCAM_DOMAIN_PATTERNS):
        return True
    if FREE_HOST_RE.search(u):
        return True
    if any(p in t for p in SCAM_TITLE_PATTERNS):
        return True
    # Szablon farmy rozpoznajemy tylko razem z cienkim tekstem — żeby nie
    # blokować prawdziwego ogłoszenia, które przypadkiem używa podobnej frazy.
    if body and len(body) <= THIN_TEXT_MAX:
        if any(p in body for p in SCAM_TEXT_PATTERNS):
            return True
    return False


def is_thin(url: str | None, text: str | None) -> bool:
    """Ogłoszenie zbyt cienkie, by ufać jego ocenie fit (<=300 znaków, bez stacku).

    To NIE jest werdykt „scam" — to sygnał „nie ufaj temu fit_score".
    """
    body = (text or "").strip()
    if len(body) > THIN_TEXT_MAX:
        return False
    return not _has_tech_signal(body)


def flag_column_sql() -> str:
    return "ALTER TABLE offers ADD COLUMN scam_suspect INTEGER NOT NULL DEFAULT 0"
