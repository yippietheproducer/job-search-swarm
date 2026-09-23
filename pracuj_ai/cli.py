"""pracuj-ai command line interface."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime

from .browser import fetch_offer_text, search_offers
from .discover import load_job_folder
from .monitor import monitor_loop, run_once
from .profile import load_profile
from .subsidies import load_companies, scan
from .tailor import analyze, render_markdown
from . import apply
from . import boards


def _cmd_tailor(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    job_path = pathlib.Path(args.job)
    if not job_path.exists():
        print(f"brak pliku z ofertą: {args.job}", file=sys.stderr)
        return 2
    job_text = job_path.read_text(encoding="utf-8")
    result = analyze(profile, job_text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))
    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[zapisano JSON → {args.out}]", file=sys.stderr)
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    offers = search_offers(args.keyword, city=args.city, limit=args.limit)
    if not offers:
        print("brak ofert (sprawdź keyword/miasto)", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for n, o in enumerate(offers, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", (o["title"] or f"offer-{n}").lower())[:50]
        path = out / f"{n:02d}_{slug}.txt"
        print(f"[pracuj-ai] pobieram {n}/{len(offers)}: {o['title'][:60]}", file=sys.stderr, flush=True)
        try:
            text = fetch_offer_text(o["url"])
        except Exception as e:  # noqa: BLE001
            text = f"# BŁĄD pobierania: {e}\nURL: {o['url']}"
        path.write_text(
            f"# TYTUŁ: {o['title']}\n# URL: {o['url']}\n\n{text}\n", encoding="utf-8"
        )
        index.append({"n": n, "title": o["title"], "url": o["url"], "file": str(path), "remote": o.get("remote", False)})
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[pracuj-ai] zapisano {len(index)} ofert → {out}/", file=sys.stderr)
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    jobs = load_job_folder(args.folder)
    if not jobs:
        print(f"brak ofert (.txt) w folderze: {args.folder}", file=sys.stderr)
        return 2
    for name, job_text in jobs:
        print(f"\n{'='*70}\n# OFERTA: {name}\n{'='*70}\n")
        result = analyze(profile, job_text)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render_markdown(result))
    return 0


def _cmd_auto(args: argparse.Namespace) -> int:
    """Fetch offers AND analyze each one, producing a ranked report.

    Automates the boring part: open pracuj.pl, grab live offers, run the AI
    tailor on every one. Still human-in-the-loop: nothing is sent, you decide.
    """
    profile = load_profile(args.profile)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"[pracuj-ai] szukam ofert: {args.keyword}{('/'+args.city) if args.city else ''}",
        file=sys.stderr,
    )
    offers = search_offers(args.keyword, city=args.city, limit=args.limit)
    if not offers:
        print("[pracuj-ai] brak ofert (sprawdź keyword/miasto)", file=sys.stderr)
        return 2
    print(f"[pracuj-ai] znaleziono {len(offers)} ofert — pobieram i analizuję...", file=sys.stderr)
    rows: list[dict] = []
    for i, off in enumerate(offers, 1):
        slug = (off.get("slug") or f"oferta-{i}")[:60]
        fname = f"{i:02d}_{slug}.txt"
        fpath = out / fname
        print(f"[pracuj-ai] ({i}/{len(offers)}) pobieram: {off['title'][:55]}", file=sys.stderr, flush=True)
        try:
            text = fetch_offer_text(off["url"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! błąd pobierania: {e}", file=sys.stderr)
            continue
        fpath.write_text(
            f"# TYTUŁ: {off['title']}\n# URL: {off['url']}\n\n{text}\n", encoding="utf-8"
        )
        print("  -> analizuję (AI)...", file=sys.stderr, flush=True)
        try:
            res = analyze(profile, text)
        except Exception as e:  # noqa: BLE001
            print(f"  ! błąd analizy: {e}", file=sys.stderr)
            res = None
        rows.append({"n": i, "title": off["title"], "url": off["url"], "file": str(fpath), "analysis": res})
    index = [
        {
            "n": r["n"],
            "title": r["title"],
            "url": r["url"],
            "file": r["file"],
            "fit_score": (r["analysis"] or {}).get("fit_score"),
        }
        for r in rows
    ]
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_report(out / "raport.md", args, rows)
    ok = sum(1 for r in rows if r["analysis"])
    print(f"[pracuj-ai] gotowe: {ok} przeanalizowanych ofert → {out}/raport.md", file=sys.stderr)
    return 0


def _write_report(path: pathlib.Path, args: argparse.Namespace, rows: list[dict]) -> None:
    rows = [r for r in rows if r["analysis"]]
    rows.sort(key=lambda r: r["analysis"].get("fit_score", 0), reverse=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L: list[str] = [
        f"# Raport ofert — {args.city} / {args.keyword or 'wszystkie'}",
        f"_wygenerowano: {now} · {len(rows)} ofert · model: human-in-the-loop_",
        "",
        "| # | Fit | Stanowisko | Link |",
        "|---|-----|-----------|------|",
    ]
    for r in rows:
        a = r["analysis"]
        L.append(f"| {r['n']} | {a.get('fit_score')} | {r['title']} | [link]({r['url']}) |")
    L.append("")
    for r in rows:
        a = r["analysis"]
        L.append(f"## {r['n']}. {r['title']} — {a.get('fit_score')}/100")
        L.append(f"URL: {r['url']}")
        L.append("")
        L.append(f"**Dopasowanie:** {a.get('fit_score')}/100")
        if a.get("fit_reason"):
            L.append("")
            L.append(a["fit_reason"])
        L.append("")
        L.append("**Mocne strony:**")
        for s in a.get("strengths", []) or []:
            L.append(f"- {s}")
        L.append("")
        L.append("**Luki (do uczciwego zakomunikowania):**")
        for g in a.get("gaps", []) or []:
            L.append(f"- {g}")
        L.append("")
        L.append("**List motywacyjny:**")
        L.append(a.get("list_motywacyjny", ""))
        L.append("")
        L.append("**Punkty do CV:**")
        for b in a.get("cv_bullets", []) or []:
            L.append(f"- {b}")
        sc = a.get("screening") or {}
        if sc:
            L.append("")
            L.append("**Sugerowane odpowiedzi (screening):**")
            for k, v in sc.items():
                L.append(f"- {k}: {v}")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def _cmd_funded(args: argparse.Namespace) -> int:
    companies = load_companies(args.companies)
    print(
        f"[pracuj-ai] przeszukuję oferty w {len(companies)} dofinansowanych firm"
        f"{' (tylko zdalne)' if args.remote else ''}...",
        file=sys.stderr,
    )
    from .store import Store, OfferRecord, offer_id

    rows = scan(companies, city=args.city, remote=args.remote, limit=args.limit)
    total = sum(len(r["offers"]) for r in rows)
    print(f"\n=== OFERTY W DOFINANSOWANYCH FIRMACH ({total} znalezionych) ===\n")
    store = Store(args.db)
    stored = 0
    profile = load_profile(args.profile) if args.score else None
    for r in rows:
        tag = "  🌐" if args.remote else ""
        print(f"## {r['company']}{tag}")
        if r["note"]:
            print(f"   _dofinansowanie: {r['note']}_")
        if not r["offers"]:
            print("   (brak aktualnych ofert)")
            continue
        for o in r["offers"]:
            z = "🌐 " if o.get("remote") else "   "
            print(f"   {z}{o['title']}")
            print(f"      {o['url']}")
            res = None
            if args.score:
                try:
                    text = fetch_offer_text(o["url"])
                    res = analyze(profile, text)
                    print(f"      → fit {res.get('fit_score')}/100")
                except Exception as e:  # noqa: BLE001
                    print(f"      ! błąd: {e}")
            oid = offer_id(o["url"])
            existing = store.get(oid)
            rec = OfferRecord(
                id=oid,
                title=o.get("title", ""),
                url=o.get("url", ""),
                company=r["company"],
                remote=o.get("remote", False),
                funded=True,
                funding_note=r.get("note", ""),
                # When not scoring, keep any fit already in the DB (don't wipe).
                fit_score=(res or {}).get("fit_score")
                if args.score else (existing.fit_score if existing else None),
                strengths=(res or {}).get("strengths", []),
                gaps=(res or {}).get("gaps", []),
                letter=(res or {}).get("letter", ""),
                cv_bullets=(res or {}).get("cv_bullets", []),
                screening=(res or {}).get("screening", {}),
            )
            if store.upsert(rec):
                stored += 1
        print()
    store.close()
    print(f"[zapisano {stored} nowych ofert (funded=1) → {args.db}]")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone, timedelta
    from .store import Store

    store = Store(args.db)
    since_iso = None
    if args.since_hours:
        since_iso = (datetime.now(timezone.utc) - timedelta(hours=args.since_hours)).isoformat(timespec="seconds")
    recs = store.list_offers(
        remote_only=args.remote,
        min_fit=args.min_fit,
        funded_only=args.funded,
        since=since_iso,
        limit=args.limit,
    )
    st = store.stats()
    print(f"=== RAPORT OFERT (db: {args.db}) ===")
    print(
        f"filtry: funded={'tak' if args.funded else 'nie'}, "
        f"zdalne={'tak' if args.remote else 'nie'}, min_fit={args.min_fit}, "
        f"since={args.since_hours}h\n"
        f"(wszystkie: {st['total']}, funded: {st['funded']}, nowe: {st['new']}, avg_fit: {st['avg_fit']})"
    )
    if not recs:
        print("(brak ofert dla tych filtrów)")
        store.close()
        return 0
    for i, r in enumerate(recs, 1):
        tags = ""
        if r.remote:
            tags += "\U0001F310 "
        if r.funded:
            tags += "[F] "
        fit = r.fit_score if r.fit_score is not None else "-"
        fs = r.first_seen[:10] if r.first_seen else "-"
        print(f"{i:2}. {fit:>3}  {tags}{r.company or ''} \u2014 {r.title}  ({fs})")
        print(f"     {r.url}")
        if r.funding_note:
            print(f"     dofinansowanie: {r.funding_note}")
    store.close()
    return 0


def _cmd_apply_prepare(args: argparse.Namespace) -> int:
    import json as _json
    from .store import Store

    materials = None
    if args.materials:
        materials = _json.loads(pathlib.Path(args.materials).read_text(encoding="utf-8"))
    offer_url = args.url
    if not offer_url and args.offer_id:
        store = Store(args.db)
        rec = store.get(args.offer_id)
        if rec is None and str(args.offer_id).isdigit():
            recs = store.list_offers(limit=1000)
            idx = int(args.offer_id) - 1
            if 0 <= idx < len(recs):
                rec = recs[idx]
        store.close()
        if rec is None:
            print(f"nie znaleziono oferty: {args.offer_id}", file=sys.stderr)
            return 2
        offer_url = rec.url
        if not materials:
            materials = {
                "list_motywacyjny": rec.letter or "",
                "screening": rec.screening or {},
            }
    if not offer_url:
        print("podaj --url lub --offer-id", file=sys.stderr)
        return 2
    if not materials:
        materials = {"list_motywacyjny": "", "screening": {}}
    res = apply.prepare(offer_url, materials, args.db)
    print(f"[apply] przygotowano (BEZ wysłania): {offer_url}")
    print(f"  form_url  : {res.get('form_url')}")
    print(f"  wiadomość : {'wypełniono' if res.get('filled_msg') else 'BRAK pola'}")
    print(f"  screening : {res.get('screening_filled', 0)} pól wypełniono")
    print(f"  screenshot: {res.get('screenshot')}")
    print("  -> przejrzyj formularz w ego i wpisz 'wyślij' aby wysłać.")
    return 0


def _cmd_apply_send(args: argparse.Namespace) -> int:
    print("[apply] WYSIŁANIE — ostatnia akcja przed wysłaniem do pracodawcy", file=sys.stderr)
    res = apply.send(args.offer_id, "offers.db")
    print(f"[apply] WYSŁANO: url_after={res.get('url_after')} screenshot={res.get('screenshot')}")
    return 0


def _cmd_apply_status(args: argparse.Namespace) -> int:
    st = apply.status()
    if not st:
        print("(brak przygotowanych aplikacji)")
        return 0
    for oid, v in st.items():
        print(f"{oid[:10]}… {v.get('status','?'):8} {v.get('offer_url','')[:62]}")
    return 0


def _cmd_app_add(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    p = Pipeline(args.db)
    try:
        app_id = p.add(args.offer_id, args.company, args.role, args.cv_variant, args.notes or "")
    except Exception as e:
        print(f"[app] BŁĄD zapisu: {e}")
        return 1
    print(f"[app] #{app_id} zapisane: {args.company} — {args.role} (sent)")
    return 0


def _cmd_app_status(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline, STATES
    p = Pipeline(args.db)
    states = tuple(args.states.split(",")) if getattr(args, "states", None) else STATES
    rows = p.list(states)
    if not rows:
        print("[app] brak aplikacji w stanach:", ",".join(states))
        return 0
    for r in rows:
        print(f"#{r['id']:>3} {r['state']:<10} {r['applied_at'][:10]} {r['company']} — {r['role']} ({r['cv_variant']})")
    return 0


def _cmd_app_transition(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    p = Pipeline(args.db)
    try:
        p.transition(args.id, args.state)
        print(f"[app] #{args.id} -> {args.state}")
    except (ValueError, LookupError) as e:
        print(f"[app] BŁĄD: {e}")
        return 1
    return 0


def _cmd_followups(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    p = Pipeline(args.db)
    due = p.followups_due()
    if not due:
        print("[followup] nic nie wymaga follow-upu. 🎉")
        return 0
    scen_file = {1: "FOLLOWUP_MATRIX.md #1", 2: "FOLLOWUP_MATRIX.md #2",
                 "nudge": "nudge po screeningu"}
    for d in due:
        r = d["row"]
        print(f"[followup] {r['company']} — {r['role']} | {d['days']} dni | szablon: {scen_file.get(d['scenario'], d['scenario'])}")
    return 0


def _cmd_funnel(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    f = Pipeline(args.db).funnel()
    print(f"aplikacje: {f['total']} | response_rate: {f['response_rate']} | interview_rate: {f['interview_rate']}")
    for s, c in f["by_state"].items():
        print(f"  {s:<12} {c}")
    return 0


def _cmd_boards(args: argparse.Namespace) -> int:
    from .store import Store, OfferRecord
    from .profile import load_profile

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    offers = boards.fetch_all(keywords, sources=sources, limit=args.limit)
    print(f"[boards] pobrano {len(offers)} pasujących ofert z: {args.sources or 'wszystkie'}", file=sys.stderr)
    store = Store(args.db)
    profile = load_profile(args.profile) if args.score else None
    scored = 0
    for o in offers:
        rec = OfferRecord(
            id=o["id"], title=o["title"], url=o["url"], company=o["company"],
            location=o["location"], remote=True, city=o["location"],
            raw_text=o["description"][:4000], source=o["source"],
        )
        if args.score and scored < args.score_limit:
            try:
                res = analyze(profile, o["description"][:4500])
                rec.fit_score = res.get("fit_score")
                rec.strengths = res.get("strengths", [])
                rec.gaps = res.get("gaps", [])
                rec.letter = res.get("list_motywacyjny", "")
                rec.cv_bullets = res.get("cv_bullets", [])
                rec.screening = res.get("screening", {})
                scored += 1
                print(f"  + fit={rec.fit_score} {o['title'][:50]} [{o['source']}]", file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  ! score fail: {e}", file=sys.stderr)
        store.upsert(rec)
    store.close()
    print(f"[boards] zapisano {len(offers)} ofert (scored={scored}) → {args.db}")
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    if args.once:
        for kw in keywords:
            run_once(
                keyword=kw,
                city=args.city,
                limit=args.limit,
                profile_path=args.profile,
                db_path=args.db,
                remote_filter=args.remote,
            )
    else:
        monitor_loop(
            keywords=keywords,
            city=args.city,
            limit=args.limit,
            profile_path=args.profile,
            db_path=args.db,
            interval_min=args.interval,
            remote_filter=args.remote,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pracuj-ai",
        description="AI-asystent aplikowania o pracę (human-in-the-loop).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tailor", help="dopasuj CV/list do jednej oferty")
    t.add_argument("--profile", default="profile.yaml", help="plik profilu YAML")
    t.add_argument("--job", required=True, help="plik .txt z treścią ogłoszenia")
    t.add_argument("--json", action="store_true", help="wyjście w JSON")
    t.add_argument("--out", help="zapisz wynik JSON do pliku")
    t.set_defaults(func=_cmd_tailor)

    b = sub.add_parser("batch", help="przetwórz folder z ofertami (.txt)")
    b.add_argument("--profile", default="profile.yaml")
    b.add_argument("--folder", required=True, help="folder z ofertami .txt")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=_cmd_batch)

    f = sub.add_parser("fetch", help="pobierz oferty z pracuj.pl (tylko odczyt)")
    f.add_argument("--keyword", required=True, help="fraza, np. python, react, typescript")
    f.add_argument("--city", default=None, help="miasto (opcjonalnie), np. warszawa")
    f.add_argument("--limit", type=int, default=5)
    f.add_argument("--out", default="offers", help="folder wyjściowy")
    f.set_defaults(func=_cmd_fetch)

    a = sub.add_parser("auto", help="pobierz + przeanalizuj oferty (raport rankingowy)")
    a.add_argument("--profile", default="profile.yaml")
    a.add_argument("--keyword", required=True, help="fraza, np. python")
    a.add_argument("--city", default=None, help="miasto (opcjonalnie), np. warszawa")
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--out", default="offers", help="folder wyjściowy")
    a.set_defaults(func=_cmd_auto)

    m = sub.add_parser("monitor", help="cyklicznie monitoruj oferty (SQLite + nowe)")
    m.add_argument("--profile", default="profile.yaml")
    m.add_argument("--keyword", required=True, help="fraza(y) przecinkowo, np. python,react,typescript")
    m.add_argument("--city", default=None, help="miasto (opcjonalnie), np. warszawa")
    m.add_argument("--remote", action="store_true", help="tylko oferty zdalne/hybrydowe")
    m.add_argument("--limit", type=int, default=15, help="ofert na cykl")
    m.add_argument("--db", default="offers.db", help="plik SQLite")
    m.add_argument("--interval", type=int, default=30, help="minut między cyklami")
    m.add_argument("--once", action="store_true", help="tylko jeden cykl (bez pętli)")
    m.set_defaults(func=_cmd_monitor)

    fd = sub.add_parser("funded", help="oferty w firmach, które dostały dofinansowanie (PARP/NCBR/UE)")
    fd.add_argument("--companies", default=None, help="plik JSON z listą firm (nadpisuje listę pestkową)")
    fd.add_argument("--city", default=None, help="miasto (opcjonalnie)")
    fd.add_argument("--remote", action="store_true", help="tylko oferty zdalne/hybrydowe")
    fd.add_argument("--limit", type=int, default=10, help="ofert na firmę")
    fd.add_argument("--score", action="store_true", help="oceniaj dopasowanie przez AI (wolne)")
    fd.add_argument("--profile", default="profile.yaml")
    fd.add_argument("--db", default="offers.db", help="plik SQLite (kumuluje oferty + flagę funded)")
    fd.set_defaults(func=_cmd_funded)

    rp = sub.add_parser("report", help="raport z bazy ofert (filtry: funded/zdalne/min_fit/nowe)")
    rp.add_argument("--db", default="offers.db")
    rp.add_argument("--funded", action="store_true", help="tylko oferty z dofinansowanych firm")
    rp.add_argument("--remote", action="store_true", help="tylko zdalne/hybrydowe")
    rp.add_argument("--min-fit", type=int, default=0, dest="min_fit")
    rp.add_argument("--since-hours", type=int, default=0, dest="since_hours",
                    help="tylko oferty widziane w ostatnich N godzin (alert o nowych)")
    rp.add_argument("--limit", type=int, default=200)
    rp.set_defaults(func=_cmd_report)

    # --- apply (human-in-the-loop assisted submission) ---
    ap = sub.add_parser("apply", help="asystowana aplikacja (human-in-the-loop)")
    ap_sub = ap.add_subparsers(dest="apply_cmd", required=True)

    prep = ap_sub.add_parser("prepare", help="otwórz ofertę + wypełnij formularz (BEZ wysyłania)")
    prep.add_argument("--url", default=None, help="bezpośredni URL oferty")
    prep.add_argument("--offer-id", default=None, help="id oferty w offers.db (hash) lub numer z raportu")
    prep.add_argument("--materials", default=None, help="plik JSON z materiałami tailor (opcjonalnie)")
    prep.add_argument("--db", default="offers.db")
    prep.set_defaults(func=_cmd_apply_prepare)

    snd = ap_sub.add_parser("send", help="WYŚLIJ (TYLKO po potwierdzeniu człowieka: 'wyślij')")
    snd.add_argument("--offer-id", default=None, help="id oferty (opcjonalnie; inaczej znajdź otwarty formularz)")
    snd.set_defaults(func=_cmd_apply_send)

    stt = ap_sub.add_parser("status", help="lista przygotowanych aplikacji")
    stt.set_defaults(func=_cmd_apply_status)

    ap_add = sub.add_parser("app-add", help="zarejestruj WYSŁANĄ aplikację")
    ap_add.add_argument("--offer-id", dest="offer_id", default=None)
    ap_add.add_argument("--company", required=True)
    ap_add.add_argument("--role", required=True)
    ap_add.add_argument("--cv-variant", dest="cv_variant", default="")
    ap_add.add_argument("--notes", default="")
    ap_add.add_argument("--db", default="offers.db")
    ap_add.set_defaults(func=_cmd_app_add)

    ap_st = sub.add_parser("app-status", help="lista aplikacji wg stanów")
    ap_st.add_argument("--states", default=None, help="np. sent,screening")
    ap_st.add_argument("--db", default="offers.db")
    ap_st.set_defaults(func=_cmd_app_status)

    ap_tr = sub.add_parser("app-move", help="zmień stan aplikacji")
    ap_tr.add_argument("id", type=int)
    ap_tr.add_argument("state")
    ap_tr.add_argument("--db", default="offers.db")
    ap_tr.set_defaults(func=_cmd_app_transition)

    fu = sub.add_parser("followups", help="co dziś wymaga follow-upu")
    fu.add_argument("--db", default="offers.db")
    fu.set_defaults(func=_cmd_followups)

    fn = sub.add_parser("funnel", help="metryki lejka aplikacji")
    fn.add_argument("--db", default="offers.db")
    fn.set_defaults(func=_cmd_funnel)

    bd = sub.add_parser("boards", help="oferty z publicznych API remote (Remotive/RemoteOK/Arbeitnow)")
    bd.add_argument("--keywords", required=True, help="frazy, np. typescript,react,python")
    bd.add_argument("--sources", default=None, help="remotive,remoteok,arbeitnow (domyślnie wszystkie)")
    bd.add_argument("--limit", type=int, default=20, help="ofert na źródło/keyword")
    bd.add_argument("--score", action="store_true", help="oceniaj top oferty przez AI (wolne)")
    bd.add_argument("--score-limit", type=int, default=10, dest="score_limit")
    bd.add_argument("--profile", default="profile.yaml")
    bd.add_argument("--db", default="offers.db")
    bd.set_defaults(func=_cmd_boards)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
