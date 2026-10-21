# secnet-parttime: deep dive

A scheduled scanner that lists part-time, school-year, federal student and freelance security roles that can be done from Florida or in the Orlando area, with a separate queue for roles that are plausible but unproven.

## Status

| Part | State | How it was checked |
|---|---|---|
| Filter rules (`src/parttime/filters.py`) | Built, classifier 2.0 | 86 cases in `tests/test_filters.py` |
| Job identity and dedupe (`identity.py`) | Built | `tests/test_lifecycle.py` |
| Lifecycle: open, missing, closed, stale, filtered, archive (`store.py`) | Built | `tests/test_lifecycle.py`, plus a 4-run mock test in `tests/test_pipeline.py` |
| Description cache (`cache.py`) | Built | Second mock run makes zero description requests (`test_cache_skips_second_download`) |
| ATS sources: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Rippling, Breezy, Recruitee | Built | Mock payloads shaped like each public API |
| Remote boards: Remotive, Jobicy, RemoteOK | Built | Remotive in the mock run; Jobicy and RemoteOK only on empty responses |
| USAJOBS | Built | Parser test. Needs `USAJOBS_API_KEY` and `USAJOBS_EMAIL` |
| Discord alerts and daily digest (`notify.py`) | Built | `tests/test_lifecycle.py` with a fake sender. Needs `DISCORD_WEBHOOK_URL` |
| Watchdog (`tools/watchdog.py`, `watchdog.yml`) | Built | Not run against a real stale repo |
| Accuracy tools (`tools/eval.py`, `tools/label.py`) | Built | Run on 8 synthetic examples only; no real labels yet |
| Dashboard (`docs/index.html`) | Built | Rendered from a mock run at 1100px and 390px |
| Live run against the real APIs | **Not run yet** | The build sandbox blocks job sites. The first GitHub Actions run is the first live test |

## Why

Summer internship lists filter on the word "intern" and a summer cycle. The roles a student can work during the semester look different: "Part-Time SOC Analyst", "Student Assistant - Cyber Defense", "Cybersecurity Intern - Spring", "IT Specialist (INFOSEC)" on a part-time schedule, or freelance pentest work. They are rare and mixed in with full-time roles.

The design aims to find every plausible school-compatible role and make the uncertainty visible, not to show only perfectly documented matches. The main list stays strict. Anything plausible but unproven goes to a review queue instead of the trash, and every decision keeps the sentence that caused it.

## How a role is judged

`filters.classify(job, cfg)` returns a `Decision` with a verdict (`listed`, `review`, `rejected`), reason codes, the evidence sentences, and what information was missing. Rejection reasons are counted in `data/stats.json` under `rejected`.

### Schedule fit

| Posting says | Fit | Where it goes |
|---|---|---|
| "15-20 hours per week" | confirmed | main list |
| "Part-time internship" (no hours) | possible, flag `Hours unverified` | main list |
| "10-25 hours per week" | possible, flag `Hours may exceed 20` | main list |
| "Minimum 25 hours per week" | rejected `hours-over-limit` | |
| "Fall internship" (no hours) | unknown | review queue |
| "Flexible hours, 40 hours per week" | rejected `hours-over-limit` | |
| "20 hours during semesters; 40 during summer" | confirmed, flag `Full-time in summer` | main list |
| "This is a full-time co-op position" | rejected `full-time` | |
| "Summer 2027 Intern" with no part-time wording | rejected `summer-only` (secnet-internships covers it) | |

Details that only showed up while building it:

- **"Full-time" usually describes the student.** Internship postings say "must be enrolled as a full-time student" or "may convert to full-time". `FT_NOT_JOB_RE` ignores those, so only a full-time *job* rejects a role.
- **Hours have seasons.** Each hours mention is tagged summer, semester or general from its own sentence. The fit comes from the first non-summer mention; a summer mention over 20 sets `summer_commitment`, which matters because it can clash with the Summer 2027 internship.
- **"20 hours during semesters" has no "per week"** but means it, so a third pattern catches hours tied to a term.

### Security work

| Title type | Example | Description fetched? | Decided by |
|---|---|---|---|
| Strict | SOC Analyst, Cybersecurity Intern, IAM Intern | yes | the title |
| Ambiguous + student signal | Technology Risk Intern, IT Student Assistant | yes | 3 or more distinct security duties in the description (`DUTY_TERMS`) |
| Ambiguous at a priority employer | Network Intern at L3Harris | yes | duties; with no description it goes to review |
| Help desk, physical security, software engineering | Help Desk Student Assistant, Security Officer | no | rejected |

Priority employers live in `data/config.json` (`priority_employers`) and are matched as whole words, so "saic" does not match "Mosaic". On Workday, priority employers also get the wider search described below.

### Can it be done from Kissimmee?

| Evidence | `florida_eligibility` | Result |
|---|---|---|
| Orlando-area location (list in `data/config.json`) | yes | listed |
| "must reside in ... Florida ..." | yes | listed |
| "must reside in California, Oregon or Washington" | no | rejected `florida-ineligible` |
| "unable to hire in Florida, Alaska or Hawaii" | no | rejected `florida-ineligible` |
| "Remote - United States", no narrower rule | likely | listed |
| "Remote" only | unknown | listed, flag `Location unverified` |
| "Remote - New York" | unknown | listed, flag `Check state eligibility` |
| Remote limited to another country | | rejected |
| Hybrid or on-site outside the Orlando area | | rejected `location` |

Only explicit applicant rules count. An office list ("offices in London, Toronto and Austin") is not a restriction.

### Experience and seniority

Seniority is judged on the title only, with whole-word matches: "Identity Management Intern" is not a manager, "Leadership Development" is not a lead, "Threat Intelligence" is not level III. "Support senior analysts" in a description has no effect.

Experience sentences are graded. "Requires 5 years of experience" rejects (`experience-required`, with the sentence saved). "5 years preferred" only flags `5+ yrs preferred`. "2 years, or equivalent combination of education" flags `2 yrs or equivalent`. "Frameworks developed over the past 5 years" is not an experience requirement. Federal roles skip the years check, but always carry `Federal: check eligibility` and their USAJOBS hiring paths, because federal qualification rules are judged from the announcement, not here.

## Job lifecycle

| State | Meaning | Shown? |
|---|---|---|
| open | seen in the latest read | yes |
| missing | absent from a complete read of every board it lives on | yes, flag `May be closed` |
| closed | absent from 2 complete reads at least 12 hours apart | no, archived after 21 days |
| stale | not seen for 4 days because its source keeps failing or only reads partially | yes, flag `Unverified`; archived after 30 days |
| filtered | still posted, but no longer passes the rules | no, archived after 21 days |

A read is **complete** only when every page of every search came back. A Workday search that hit the page cap, a SmartRecruiters query that hit 5 pages, a full Jobicy page, and every RemoteOK read (a recent-only feed) count as **partial**. Partial reads refresh `last_seen` for roles they return and `last_checked` for the board, but never move a role toward closed. A failed read changes nothing at all.

Closing is also paused for one run whenever the search scope changes (search terms or the priority list), because absence after a scope change proves nothing. The scope fingerprint is stored in `stats.json`.

`data/archive.jsonl` keeps a compact row for every role that leaves the store. Over time it shows which employers actually post school-year security work.

## Identity

`identity.group` merges listings that are the same job, strongest evidence first: same source job id, same employer plus requisition id, same canonical apply URL (tracking parameters stripped), then same company, title and place. The last rule never merges two different ids from the same board, or two different requisition ids, because those are two openings. The employer's own board wins over a remote-board repost as the primary; the others are kept as `alternate_sources`.

## Alerts

`notify.py` posts to Discord when `DISCORD_WEBHOOK_URL` is set.

- **Immediately:** each new role on the main list with a confirmed fit.
- **Daily digest** (first run after 8am Eastern): new possible fits, new review items, and material changes to anything already sent. A change is a different fingerprint over title, hours, pay, place, fit, queue and flags.
- **First run:** records everything as already seen and sends one "tracker is live" message, so the backlog does not flood the channel.

Notification state lives in `data/notify_state.json`, separate from the job store, and is saved after each successful send, so a retried run does not resend. One gap remains: if a run sends and then fails before its commit, the next run can resend those roles.

`tools/watchdog.py` runs daily on its own schedule. If `stats.json` is older than 18 hours it posts to Discord and fails, which makes GitHub email the repo owner. GitHub can delay or drop scheduled runs, so the 6-hour schedule is a target, not a guarantee.

## Requests

The pre-check (`pipeline.make_want`) decides which roles are worth a description: a strict or gated title, and a location that could fit. Lever, Ashby, Recruitee and the remote boards include descriptions in the list, so they cost nothing extra. Greenhouse re-reads the board once with `content=true` only when a wanted role is missing from the cache. Workday and SmartRecruiters fetch per role.

`cache.py` stores the text of every description fetched, kept or rejected, for up to 8,000 characters. An entry is reused when the source's change marker matches (Greenhouse `updated_at`, SmartRecruiters `releasedDate`) or, for Workday, when it is under 7 days old. Rule changes re-run against cached text without downloads. The cache is carried between GitHub Actions runs with `actions/cache` and is not committed.

Concurrency is bounded: 32 boards at once, 8 requests per host, 150 seconds per board, 3 retries with backoff on 429 and 5xx (`net.py`, from the upstream engine).

### Workday

| Pass | Search | Who |
|---|---|---|
| A | "cyber", "security" through the tenant's own Part-time facet | every tenant that has one |
| A fallback | same terms without the facet | tenants with no facet, or when the facet query fails |
| B | "intern" | every tenant |
| B extra | "student", "co-op", "information technology", and the security terms without the facet | priority employers |

Results merge by job id. `stats.json` counts each outcome separately under `workday_outcomes`: `tenant-unavailable`, `facet-found`, `facet-missing`, `facet-query-failed`, `zero-matches`, `capped`, `page-failed`.

## Measuring accuracy

`data/decisions.jsonl` is rewritten every run with every kept role and a stable sample of 300 rejected candidates (roles that passed the title pre-check). Each row has `job_id`, `decision`, `reason_codes`, `matched_evidence`, `missing_information` and `classifier_version`. Reading the rejected sample is how you catch a filter that throws away good roles.

To measure it:

```bash
python run.py                                   # local run fills the description cache
python tools/label.py "greenhouse:acme:123" fit # repeat for 30+ real roles: fit / review / reject
python tools/eval.py                            # precision, recall, reach, every disagreement
```

Precision is the share of main-list roles labeled fit. Recall is the share of fit labels that reached the main list. Reach is the share of fit and review labels that appeared anywhere. These numbers describe the labeled sample, so they compare rule versions against each other; they cannot estimate how many roles the whole job market hides from the tracker. Until real labels exist, `tools/eval.py` runs on 8 synthetic examples in `tests/labeled_examples.jsonl`.

## Scope

**Covers:** the ATS boards in the shared registry that use the eight connectors above, three remote boards, and USAJOBS.

**Does not cover:** Workable (its anonymous API returns a provider-wide 429 to bulk readers), Oracle, iCIMS, Eightfold, LinkedIn, Indeed and Handshake. Several priority employers are not in the shared registry at all (L3Harris, Lockheed Martin, SAIC, Peraton, ManTech); add their career boards to `data/companies_extra.json` in the same shape as `companies.json`. UCF student jobs only appear if UCF posts them on its Workday `careers` site.

**Known limits:**
- Greenhouse lists carry no employment type, so its part-time evidence comes from the title and description.
- Workday tenants often block cloud IPs. Set `WORKDAY_PROXY` if `workday_outcomes` shows many `tenant-unavailable`.
- Duty matching counts keywords. A help desk posting that names three security tools would pass; a security role that uses none of the listed words would not.
- State restrictions are read only from explicit "must reside / can hire / cannot hire" sentences.

## Decisions worth defending

**Separate repo, shared registry.** The secnet engine is built around intern seasons. Part-time roles have no season, so reusing it meant fighting that logic. `tools/sync_companies.py` pulls `data/companies.json` from secnet-internships each run so both trackers scan the same boards.

**Unknown goes to review, not the trash.** v1 treated "Fall internship" as part-time with no hours evidence, which put unproven roles on the main list. v2 splits fit into confirmed, possible and unknown. Verified by `test_fall_internship_goes_to_review`.

**Only complete reads close roles.** v1 closed a role whenever its board answered without it, so a capped Workday search could close a real job. v2 needs two complete reads 12 hours apart. Verified by `test_closing_needs_two_complete_scans_12h_apart` and `test_partial_read_never_closes`.

**A wanted role with no description is left alone.** If a description fetch fails, the role is neither kept nor marked filtered that run, so one failed detail page cannot knock a real role off the list (`reasons["unchecked-no-description"]` in `pipeline.run`).

## Architecture

```
data/companies.json ─► ATS fetchers ──┐   (pre-check → cached or fetched description)
remote boards ────────────────────────┼─► filters.classify ─► identity.group ─► store.update
USAJOBS ──────────────────────────────┘     (listed / review / rejected)        (lifecycle)
                                                                                      │
   README.md ◄─ render     docs/jobs.json (dashboard)     data/jobs.json, jobs.csv ◄──┤
   data/decisions.jsonl    data/archive.jsonl             data/stats.json             │
   Discord ◄─ notify (data/notify_state.json) ◄───────────────────────────────────────┘
```

Every source returns `Job` records and a `Fetch` saying whether the read was ok and complete. Nothing downstream knows which API a role came from. Each board runs under a timeout and an exception guard (`pipeline._bounded`), so one bad board is a counted failure, not a failed run.

## Running it

```bash
pip install -r requirements.txt
python run.py --limit 50          # quick test on 50 boards
python run.py --sources remote    # remote boards only
python run.py                     # full scan
python -m pytest -q               # offline tests
```

Repo secrets for the workflows, all optional: `USAJOBS_API_KEY` and `USAJOBS_EMAIL` (free key at developer.usajobs.gov), `DISCORD_WEBHOOK_URL`, `WORKDAY_PROXY`.

## Testing

`python -m pytest -q` runs 109 tests offline. They cover the filter rules, each parser's field mapping, Workday's two passes, the cache, identity, the full lifecycle across runs, and alert timing and retries. They cannot catch an API whose real response differs from the documented shape. After the first live run, check `boards`, `error_samples` and `workday_outcomes` in `data/stats.json`.

## Layout

| Path | What |
|---|---|
| `src/parttime/filters.py` | All keep, review and drop rules |
| `src/parttime/identity.py` | Which listings are the same job |
| `src/parttime/store.py` | Lifecycle and archive |
| `src/parttime/cache.py` | Description cache |
| `src/parttime/notify.py` | Discord alerts and digest |
| `src/parttime/sources/ats.py` | Company board connectors |
| `src/parttime/sources/remote_boards.py` | Remotive, Jobicy, RemoteOK |
| `src/parttime/sources/usajobs.py` | Federal search |
| `src/parttime/pipeline.py` | One run, end to end |
| `src/parttime/render.py` | README tables |
| `src/parttime/net.py` | Retries and per-host limits (from the upstream engine, MIT) |
| `docs/index.html` | Dashboard (GitHub Pages from `/docs`) |
| `data/config.json` | Orlando-area places, priority employers, hour limit |
| `data/decisions.jsonl` | Last run's decisions with evidence |
| `data/archive.jsonl` | History of roles that left the list |
| `tools/eval.py`, `tools/label.py` | Accuracy measurement |
| `tools/watchdog.py` | Staleness alarm |
| `.github/workflows/update.yml` | Scan every 6 hours and commit |
| `.github/workflows/watchdog.yml` | Daily freshness check |
