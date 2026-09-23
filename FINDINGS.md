# FINDINGS — what six weeks of measured crawling actually showed

Every claim below is backed by a captured HTTP response or a stored raw payload
(`crawl_raw.json` exists so filters can be re-run offline — re-hammering ~450
endpoints to iterate on a filter is both rude and slow).

## 1. The justjoin `b2b_contract` facet blind spot

justjoin.it's API facets are real (`workingTimes=part_time`,
`remoteWorkOptions=remote`, `experienceLevels=junior|mid` all filter
correctly). But `workingTime` is an enum, and the remote part-time market
does not live under `part_time`:

| `workingTimes` value | Offers (measured) |
|---|---|
| `part_time` | 104 |
| `full_time` | 9,259 |
| **`b2b_contract`** | **1,304** |
| `freelance` | 128 |
| `internship` | 20 |

`b2b_contract + remote` = **649 rows** (`+ mid` = 193). A role titled
*"Shopify Consultant (part-time)"* whose prose says *"15–30 godzin
tygodniowo, 100% zdalnie"* carries `workingTime: b2b_contract` — so **none
of those 649 rows can ever appear in a `part_time` sweep.** "All remote
part-time roles = 104" was never the market; it was one category of it.

Pagination stability was checked before blaming it: the same facet fetched
twice returned identical sets. The exclusion is structural, not an artifact.

**Lesson:** on any board, enumerate the *whole enum* of a facet before
trusting a filtered count. The category you're not querying is invisible.

## 2. Silent no-op filters — the bug class, eight confirmed instances

A filter that returns HTTP 200 with unfiltered data looks exactly like
success. Confirmed instances (each verified by sending two different filter
values and diffing the result sets):

| Source | Filter | Reality |
|---|---|---|
| remotive.com | `search`, `category`, `limit` | no-ops — same payload regardless |
| arc.dev/remote-jobs | `?jobTypes[]=part-time`, `?jobType=`, `?search=` | all no-ops — always the same 30 rows. Real data lives in `__NEXT_DATA__.props.pageProps.arcJobs[]` (`availableHoursPerWeek`, `experienceLevel`) |
| jobgether.com | `&search=` | no-op — identical 50 rows for `react` / `python` / `next.js`. (`?contractType=part-time` *does* work) |
| justjoin.it | `perPage` | ignored — always exactly 10 rows; only `from=N` advances, and `meta.totalItems` is the authoritative count |
| jobs.workable.com `/api/v1/jobs` | query params | returns **HTTP 200** with a payload that ignores the query — the data is behind a different endpoint |
| tech.eu feed | `?paged=N` | 200 but pages 1/2/3 are byte-identical, 100% overlap |

**Rule adopted:** "the filter returned results" is unproven until two
different filter values return different result sets. There is a regression
test for each of these.

## 3. The 429 penalty box (why per-host semaphores)

Burst-sweeping a single ATS host at 12 workers got this IP into a shared
rate-limit penalty box: **429 on 434/434 slug probes for 30+ minutes**,
poisoning every unrelated request to that host. A global 128-way hammer would
do that to nine hosts at once. Hence `funding_swarm.py`'s design: 128 workers
across hosts, a semaphore of 4 *per hostname*. Politeness here is not
etiquette, it's throughput.

## 4. The units trap

Extracting salary ranges from contract rows produced `25,000–30,000 zł/h`.
Absurd — so check the raw payload, not the summary:

```
TQLO Shopify Consultant   unit='Hour'   from=30240  fromPerUnit=180.00   → 180 zł/h  ✓
EduGO Engineering Manager unit='Month'  from=25000  fromPerUnit=25000    → 25,000 zł/MONTH
```

Same `from` field, different `unit`. Normalizing on `fromPerUnit` (and
failing loud when `unit` is unknown) is the difference between a ranked
list and garbage. Two more unit traps of the same class were caught the
same way; each is now a test.

## 5. Crawl coverage (what one deterministic pass costs)

`swarm/crawl_sources.py` — 13 source families, ~450 requests, 64 workers,
~4 minutes wall clock, **no LLM**:

| Source family | Endpoints | Postings returned |
|---|---|---|
| Greenhouse | 73 tokens | 4,963 |
| Ashby | 54 orgs | 2,294 |
| Lever | 61 slugs | 623 |
| Arbeitnow | 15 pages | 938 |
| Workable jobs API | 17 queries × 4 locations | 599 |
| Personio XML | 39 companies | 422 |
| Recruitee | 39 tenants | 101 |
| Rocketjobs | facet + paging | 190 |
| Justjoin | 3 facet grids × paging | 95 |
| Workable widget API (carries the body) | 40 accounts | 52 |
| Simple feeds (jobicy, remoteok, remotive, weworkremotely…) | 8 feeds | 321 |

Why deterministic instead of "more agents": none of this needs a model — it
needs a fetch, a regex, and a rule. LLM lanes were reserved for judgement
(fit scoring, gap naming); the bulk collection parallelises for free.

## 6. Dead ends, recorded so nobody retries them

- **403 bot-hostile (alive):** toptal, himalayas, useme
- **404/gone:** arc.dev/talent/jobs, codementor jobs, x-team boards,
  braintrust, otta.com/jobs
- **200 but hollow:** jobs.x-team.com (1 MB JS shell, zero server-rendered
  roles), app.usebraintrust.com (209-char shell), welcometothejungle
  (**202 with a 0-byte body**), gun.io (marketing page, no job cards)
- **A lane can misreport its own runtime:** one sweep's report header
  claimed a different model than its own log file showed. Self-description
  is not evidence — read the artefact.
