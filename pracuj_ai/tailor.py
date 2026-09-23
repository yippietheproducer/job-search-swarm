"""AI tailoring engine: fit scoring + personalised application materials.

Given the candidate profile and a job description, the fleet model produces:
  - fit_score (0-100) with a short rationale
  - a tailored Polish cover letter (list motywacyjny)
  - CV bullet adjustments that foreground the relevant experience
  - suggested answers to common pracuj.pl screening questions

The model is explicitly instructed NEVER to invent experience or skills.
"""
from __future__ import annotations

import json
import os
import re
import sys

from .fleet import FleetClient, FleetError
from .profile import profile_to_text

# Priority order of backends. hy3-free (zen, free) is preferred when up;
# cline-fleet's deepseek/kimi are reliable fallbacks. Override with the
# PRACUJ_GATEWAY / PRACUJ_MODEL env vars (single backend).
DEFAULT_BACKENDS = [
    {"gateway": "https://opencode.ai/zen/v1", "model": "hy3-free", "api_key": "", "max_tokens": 9000},
    {"gateway": "http://localhost:8443", "model": "cline-pass/deepseek-v4-flash", "api_key": "fleet-managed", "max_tokens": 3500},
    {"gateway": "http://localhost:8443", "model": "cline-pass/kimi-k3", "api_key": "fleet-managed", "max_tokens": 3500},
]

SYSTEM = """\
Jesteś doświadczonym polskim rekruterem oraz specjalistą od personal brandingu \
na rynku IT. Twoim zadaniem jest ocenić dopasowanie kandydata do ogłoszenia i \
wygenerować spersonalizowane materiały rekrutacyjne.

ŻELAZNE ZASADY:
1. Nie zmyślaj. Używaj TYLKO tego, co jest w profilu kandydata. Jeśli ogłoszenie \
wymaga czegoś, czego kandydat nie ma — napisz o tym uczciwie (np. "chęć szybkiego \
nauczenia się X"), nigdy nie kłam o doświadczeniu ani umiejętnościach.
2. List motywacyjny: język polski, profesjonalny, konkretny, 200-300 słów. \
Odnieś się bezpośrednio do wymagań z ogłoszenia.
3. cv_bullets: 3-4 punkty do wpisania w CV, podkreślające najbardziej \
trafne doświadczenie kandydata pod to konkretne stanowisko.
4. screening: sugerowane odpowiedzi (tak/nie/liczba/tekst) na typowe pytania \
rekrutacyjne pracuj.pl (np. "Czy posiadasz X?", "Oczekiwane wynagrodzenie").

KONTEKST KANDYDATA (kluczowe do uczciwej oceny fit):
- Kandydat jest JUNIOR-LEVEL samoukiem-budowniczym. NIE ma etatowego doświadczenia \
  w firmie i SAM OKREŚLA SIĘ JAKO JUNIOR (nie senior). Całe doświadczenie to \
  budowanie WŁASNYCH, działających aplikacji i narzędzi (portfolio 50+ repozytoriów: \
  m.in. BeatVids.app — production SaaS z realnymi użytkownikami, agent-os, psyloom, \
  MAILSCRAPER, WEBSAMPLER).
- PRZY OCENIE FIT:
  - Dla ról JUNIOR / MID / TRAINE / STAŻ → kandydat ma SILNE dopasowanie \
    (portfolio, full-stack hands-on, integracja AI, gotowość do nauki, szybki ramp-up).
  - Dla ról SENIOR → UCZCIWA LUKA: brak doświadczenia seniorskiego (przywództwo \
    zespołowe, skala enterprise, mentoring, głębia domeny korporacyjnej). Fit NIŻSZY; \
    list powinien uczciwie mówić o poziomie junior i chęci szybkiego rozwoju — NIGDY \
    nie zawyżać seniority.
- WYSOKO WAŻ demonstracyjne dowody potencjału: dowiezione produkty, własność \
  end-to-end, full-stack delivery, infra/CI/testy, integracja AI.
- NIGDY nie zmyślaj etatowego doświadczenia w firmie ani poziomu seniority; framing \
  to "junior z silnym portfolio własnych projektów", nie "senior".

Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{
  "fit_score": <int 0-100>,
  "fit_reason": "<krótkie uzasadnienie dopasowania>",
  "strengths": ["<mocne strony dopasowane do oferty>"],
  "gaps": ["<luki do uczciwego zakomunikowania>"],
  "list_motywacyjny": "<pełny tekst listu>",
  "cv_bullets": ["<punkt 1>", "<punkt 2>"],
  "screening": {"<pytanie>": "<odpowiedź>"}
}
"""


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the model output, tolerating prose/fences."""
    text = text.strip()
    # 1) direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2) fenced block (greedy, so nested braces inside are kept)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3) outermost {...}: first '{' to last '}'
    start = text.find("{")
    if start != -1:
        end = text.rfind("}")
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise ValueError(f"could not parse JSON from model output:\n{text[:500]}")


def _analyze_with(
    client: FleetClient,
    profile: dict,
    job_text: str,
    *,
    temperature: float = 0.4,
    json_retries: int = 1,
    max_tokens: int = 3200,
) -> dict:
    """Run the fit+tailor analysis on a single client (with JSON-retry)."""
    base_user = (
        f"PROFIL KANDYDATA:\n{profile_to_text(profile)}\n\n"
        f"OGŁOSZENIE O PRACĘ:\n{job_text}\n\n"
    )
    user = (
        base_user
        + "Wygeneruj JSON zgodnie z instrukcją. WYJŚCIE: wyłącznie blok "
        "```json ... ```. Żadnego tekstu przed ani po nim."
    )
    last_err: Exception | None = None
    for attempt in range(json_retries + 1):
        print(f"[pracuj-ai] generuję ({client.model}, próba {attempt + 1})...", file=sys.stderr, flush=True)
        try:
            raw = client.chat(SYSTEM, user, temperature=temperature, max_tokens=max_tokens)
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError, FleetError) as exc:
            last_err = exc
            max_tokens = min(max_tokens + 900, 9000)
            user = (
                base_user
                + "POPRZEDNIA ODPOWIEDŹ NIE BYŁA POPRAWNYM JSONEM (lub została "
                "ucięta). Zwróć WYŁĄCZNIE poprawny, PEŁNY JSON w bloku "
                "```json ... ```, bez żadnego wprowadzenia ani komentarza. "
                "Skróć list motywacyjny, by zmieścić się w limicie tokenów."
            )
    raise ValueError(f"backend {client.model} failed: {last_err}")


def analyze(
    profile: dict,
    job_text: str,
    *,
    backends: list[dict] | None = None,
    temperature: float = 0.4,
    json_retries: int = 1,
) -> dict:
    """Score fit and generate tailored materials for one job.

    Tries each backend in order (hy3-free first, then reliable fallbacks)
    and returns the first parseable result. Retries with a firmer
    "JSON-only" instruction if a model strays into prose.
    """
    if backends is None:
        env_gw = os.environ.get("PRACUJ_GATEWAY")
        env_model = os.environ.get("PRACUJ_MODEL")
        if env_gw and env_model:
            backends = [{"gateway": env_gw, "model": env_model,
                         "api_key": os.environ.get("PRACUJ_API_KEY", "")}]
        else:
            backends = DEFAULT_BACKENDS

    # hy3-free is a reasoning model: long job text burns its token budget on
    # reasoning and truncates the final JSON. Cap the input so it fits.
    job_text = job_text.strip()[:4500]

    last_err: Exception | None = None
    for b in backends:
        client = FleetClient(
            base_url=b["gateway"], model=b["model"],
            api_key=b.get("api_key", ""), timeout=280, max_retries=1,
        )
        try:
            return _analyze_with(client, profile, job_text, temperature=temperature, json_retries=json_retries, max_tokens=b.get("max_tokens", 3200))
        except Exception as exc:  # noqa: BLE001 - try next backend
            last_err = exc
            print(f"[pracuj-ai] backend {b['model']} zawiódł: {exc}; próbuję następny", file=sys.stderr, flush=True)
    raise ValueError(f"wszystkie backendy zawiodły: {last_err}")


def render_markdown(result: dict) -> str:
    """Render the analysis as a readable markdown brief."""
    lines = []
    lines.append(f"## Dopasowanie: {result.get('fit_score', '?')}/100")
    if result.get("fit_reason"):
        lines.append(f"\n{result['fit_reason']}\n")
    if result.get("strengths"):
        lines.append("**Mocne strony:**")
        for s in result["strengths"]:
            lines.append(f"- {s}")
        lines.append("")
    if result.get("gaps"):
        lines.append("**Luki (do uczciwego zakomunikowania):**")
        for g in result["gaps"]:
            lines.append(f"- {g}")
        lines.append("")
    if result.get("list_motywacyjny"):
        lines.append("## List motywacyjny\n")
        lines.append(result["list_motywacyjny"])
        lines.append("")
    if result.get("cv_bullets"):
        lines.append("## Punkty do CV\n")
        for b in result["cv_bullets"]:
            lines.append(f"- {b}")
        lines.append("")
    if result.get("screening"):
        lines.append("## Sugerowane odpowiedzi na pytania\n")
        for q, a in result["screening"].items():
            lines.append(f"- **{q}** → {a}")
        lines.append("")
    return "\n".join(lines)
