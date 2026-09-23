# job-search-swarm

[![tests](https://github.com/yippietheproducer/job-search-swarm/actions/workflows/tests.yml/badge.svg)](https://github.com/yippietheproducer/job-search-swarm/actions/workflows/tests.yml)

A human-in-the-loop job-search engine for the Polish / EU remote market —
deterministic crawlers, an evidence-graded store, and two swarm scripts that
find roles **before the aggregators do**.

I built this because I needed it: I'm a self-taught, AI-native builder looking
for a part-time / contract role alongside my Master's, and every board I
searched had been swept thirty times. The interesting engineering is not the
scraping — it's **what the data turned out to be lying about** (see
[`FINDINGS.md`](FINDINGS.md)).

Built with an agent fleet (I orchestrate coding agents for a living-style
workflow); every module is covered by tests I made the agents write and every
claim in `FINDINGS.md` is backed by a captured HTTP response, not a vibe.

## What's here

| Piece | What it does |
|---|---|
| `swarm/funding_swarm.py` | **Funding-news → ATS arbitrage.** Harvests funding feeds (tech.eu, Sifted, 300gospodarka, mamstartup), resolves each freshly-funded company to its *own* ATS board across ~9 platforms, and pulls live roles. A company that raised 48 h ago has roles no aggregator lists yet. |
| `swarm/hn_swarm.py` | Sweeps **12 months** of HN "Who is hiring" threads via the Algolia API (~5,000 founder-posted ads, machine-readable, no auth) and filters for remote / part-time / AI-stack. |
| `swarm/crawl_sources.py` | Deterministic crawler — no LLM in the loop. 13 source families, ~450 requests at 64 workers, ~4 min wall clock (Greenhouse, Ashby, Lever, Workable, Personio, Recruitee, justjoin, feeds…). Dumps raw rows so filters can be iterated **offline**. |
| `pracuj_ai/` | The application copilot: fetch → dedupe store (SQLite) → scam-farm filter → remote-truth audit → evidence-graded fit scoring → tailored CV/cover-letter drafts. **A human sends; the machine prepares.** |
| `tests/` | 207 test functions (240 cases + 24 subtests). Includes regression tests for every silent failure found in production data. |

## The concurrency rule that matters

`funding_swarm.py` runs 128 workers but caps **4 concurrent requests per
hostname** — a semaphore per host, never one global lock. This is a measured
rule, not a preference: burst-sweeping a single host at 12 workers got this IP
put in a shared rate-limit penalty box (429 on 434/434 probes, 30+ minutes).
Wide across hosts, narrow per host.

## Evidence discipline

The scorer reports **gaps, not just fit**. A role the candidate doesn't
qualify for gets its gap named in the output (e.g. "senior-only", "German
required", "Spark") instead of being silently dropped or oversold. The
tailoring prompt has hard honesty rules: never invent employment history,
never inflate seniority. Missing data fails loud; a guard that silently
skips a lane is treated as a bug (there's a regression test for exactly
that).

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest pyyaml
python3 -m pytest tests/ -q          # 240 passed, 1 skipped (private-data guard)

# sweep 12 months of HN "Who is hiring" for remote part-time AI roles:
python3 swarm/hn_swarm.py --out hn_all.json

# funding-news → ATS arbitrage:
python3 swarm/funding_swarm.py --workers 128 --per-host 4 --out funding_swarm.json

# the deterministic 13-source crawl (no LLM):
python3 swarm/crawl_sources.py
```

The `pracuj-ai` CLI (`fetch` / `monitor` / `funded` / `report` / `tailor`)
drives the full pipeline; `tailor` needs an LLM endpoint via
`PRACUJ_API_KEY` and a candidate profile (`profile.yaml`, see
`profile.example.yaml`). No keys are committed.

## Findings (the part worth reading)

Six weeks of measured crawling produced findings that generalize beyond my
search — e.g. the **justjoin `b2b_contract` facet blind spot** (the
`part_time` filter never returns B2B-contract roles; that's where the remote
part-time market actually lives) and **eight silent no-op filters** across
major boards (filters that return HTTP 200 with unfiltered data). Full
table with reproduction notes: [`FINDINGS.md`](FINDINGS.md).

## License

MIT — see [LICENSE](LICENSE).
