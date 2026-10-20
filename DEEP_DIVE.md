# secnet-parttime: deep dive

A scheduled scanner that lists part-time, school-year, federal student and freelance security roles that are remote (US) or in the Orlando area.

## Status

| Part | State | How it was checked |
|---|---|---|
| Filter rules (`src/parttime/filters.py`) | Built | 61 unit cases in `tests/test_filters.py` |
| ATS sources: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Rippling, Breezy, Recruitee | Built | Parsed against mock payloads shaped like each public API (`tests/test_pipeline.py`) |
| Remote boards: Remotive, Jobicy, RemoteOK | Built | Remotive covered by the mock run; Jobicy and RemoteOK only by empty-response handling |
| USAJOBS | Built | Parser test with a sample payload. Needs `USAJOBS_API_KEY` and `USAJOBS_EMAIL` secrets |
| Store, open/closed tracking, README and dashboard output | Built | Two-run mock test: a removed role closes, a failed board keeps its roles open |
| Live run against the real APIs | **Not run yet** | The build sandbox blocks outbound requests to job sites. The first GitHub Actions run is the first live test |

## Why

Summer internship lists filter on the word "intern" and a summer cycle. The roles a student can work during the semester look different: "Part-Time SOC Analyst", "Student Assistant - Cyber Defense", "Cybersecurity Intern - Spring", "IT Specialist (INFOSEC)" on a part-time schedule, or freelance pentest work. They are rare and mixed in with full-time roles, so the tracker trades recall for precision: a role needs real evidence that it is part-time before it is listed.

## The four gates

`filters.classify(job)` returns `None` (keep) or the reason it dropped the role. Reasons are counted in `data/stats.json` under `rejected`.

| Gate | Keeps | Drops | Reason code |
|---|---|---|---|
| Security title | SOC, GRC, pentest, IAM, cloud/network security, INFOSEC | Security officer/guard titles, loss prevention, "security clearance" in a SWE title, securities, sales | `not-security` |
| Seniority | Entry roles, `I`, `II` | Senior, staff, principal, lead, manager, `III`, `IV` | `senior` |
| Location | Remote with no foreign-only marker; Orlando metro list in `data/config.json` | Out-of-area on-site, remote limited to another country or region | `location` |
| Part-time evidence | Source employment type is part-time; title says part-time, fall, spring, year-round, academic year; text says part-time, school-year hours, or ≤20 hrs/week | Full-time with no part-time cue, summer-only intern titles, full-time 40-hour contracts | `not-part-time`, `summer-only`, `full-time-contract` |
| Hours | Stated hours that start at 20 or below | Stated hours that start above 20, or a single stated number above 29 | `hours` |
| Experience | Under 5 years asked (flagged at 2+) | 5+ years in the text (federal roles exempt) | `experience` |

Details that only show up when building it:

- **Part-time "security" is mostly guard work.** `PHYSICAL_RE` vetoes uniform titles unless the rest of the title names a cyber term, so "Information Security Officer Intern" survives and "Security Officer - Data Center" does not.
- **Federal student titles are generic.** "Student Trainee (Information Technology)" only passes when the duties text names cyber work (`FED_CYBER_RE`).
- **A stated full-time type loses to the title or hours.** If Lever says "Full-time" but the title says "Part-Time", the title wins.

## Scope

**Covers:** the ATS boards in the shared registry that use the eight connectors above, three remote boards, and USAJOBS.

**Does not cover:** Workable (its anonymous API returns a provider-wide 429 to bulk readers), Oracle, iCIMS, Eightfold, LinkedIn, Indeed and Handshake (no public API). On-campus UCF student jobs only appear if UCF posts them on its Workday `careers` site.

**Known limits:**
- Greenhouse lists carry no employment type, so its part-time evidence comes from the title and description only.
- Workday tenants often block cloud IPs. Set `WORKDAY_PROXY` if the stats show Workday failures.
- A role whose detail fetch fails can be classified as not part-time and close for one run. It reopens with its original `first_seen` when the next run reads it.
- Remote roles limited to one US state are not detected.

## Decisions worth defending

**Separate repo, shared registry.** The secnet engine is built around intern seasons (sticky season labels, cycle inference). Part-time roles have no season, so reusing it meant fighting that logic. `tools/sync_companies.py` pulls `data/companies.json` from secnet-internships each run instead, so both trackers scan the same boards.

**Descriptions only for candidates.** A board is listed cheaply first. Only roles that pass the title and location pre-check (`pipeline.make_want`) get a description fetch: Greenhouse re-reads the board once with `content=true`, Workday and SmartRecruiters fetch per role.

**Workday's own part-time filter.** `ats.wd_part_time_facet` finds the tenant's "Part time" facet in the first search response and re-queries with it, which is more precise than reading descriptions.

**A failed read never closes a role.** `pipeline.merge` only closes roles from boards that answered this run. Verified by `test_closing_and_failed_boards`.

## Architecture

```
data/companies.json ──► ATS fetchers ─┐
remote boards ────────────────────────┼─► filters.classify ─► dedupe ─► merge with data/jobs.json
USAJOBS ──────────────────────────────┘                                     │
                                       README.md tables ◄─ render ◄─────────┤
                                       docs/jobs.json (dashboard) ◄──────────┤
                                       data/jobs.csv, data/stats.json ◄──────┘
```

Every source returns `Job` records; nothing downstream knows which API a role came from. Each board runs under a timeout and an exception guard (`pipeline._bounded`), so one bad board is a counted failure, not a failed run.

## Running it

```bash
pip install -r requirements.txt
python run.py --limit 50          # quick test on 50 boards
python run.py --sources remote    # remote boards only
python run.py                     # full scan
```

Optional secrets for the Actions workflow: `USAJOBS_API_KEY` and `USAJOBS_EMAIL` (free key at developer.usajobs.gov), `WORKDAY_PROXY`. Without the USAJOBS pair, `stats.json` shows that source as skipped.

## Testing

`python -m pytest -q` runs 64 tests offline. They cover the filter rules, each parser's field mapping, Workday's facet path, and open/closed tracking across two runs. They cannot catch an API whose real response differs from the documented shape. Check `boards_failed` and `error_samples` in `data/stats.json` after the first live run for that.

## Layout

| Path | What |
|---|---|
| `src/parttime/filters.py` | All keep/drop rules |
| `src/parttime/sources/ats.py` | Company board connectors |
| `src/parttime/sources/remote_boards.py` | Remotive, Jobicy, RemoteOK |
| `src/parttime/sources/usajobs.py` | Federal search |
| `src/parttime/pipeline.py` | Fetch, classify, merge, write outputs |
| `src/parttime/render.py` | README tables |
| `src/parttime/net.py` | Retry and per-host limits (from the upstream engine, MIT) |
| `docs/index.html` | Dashboard (GitHub Pages from `/docs`) |
| `data/config.json` | Orlando-area place list |
| `.github/workflows/update.yml` | Scan every 6 hours and commit |
