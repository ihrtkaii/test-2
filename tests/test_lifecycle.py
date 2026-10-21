from datetime import UTC, datetime, timedelta

from parttime import identity, models, notify, store
from parttime.cache import DescCache
from parttime.models import Job


def ts(hours: float = 0) -> str:
    return (datetime(2026, 10, 21, 12, tzinfo=UTC) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def job(id, board="gh:a", **k):
    base = dict(source=id.split(":")[0], company="Acme", title="Part-Time SOC Analyst",
                location="Remote", url=f"https://x/{id}", board=board, where="Remote",
                queue=models.LISTED, schedule_fit=models.FIT_CONFIRMED)
    base.update(k)
    return Job(id=id, **base)


# ---------------------------------------------------------------- identity (review item 5)
def test_same_title_same_board_different_ids_are_two_openings():
    a, b = job("greenhouse:a:1"), job("greenhouse:a:2")
    assert len(identity.group([a, b])) == 2


def test_requisition_links_across_sources():
    a = job("workday:t:/x", board="wd", requisition_id="R9", title="Cyber Intern (Part-Time)")
    b = job("greenhouse:t:5", board="gh", requisition_id="r9", title="Cyber Intern")
    [(primary, alts)] = identity.group([a, b])
    assert {primary.id, alts[0].id} == {a.id, b.id}


def test_different_requisitions_never_merge():
    a = job("workday:t:/x", board="wd1", requisition_id="R1")
    b = job("workday:t:/y", board="wd2", requisition_id="R2")
    assert len(identity.group([a, b])) == 2


def test_tracking_params_ignored_and_aggregator_not_primary():
    a = job("remotive:9", board="remotive", url="https://boards.greenhouse.io/acme/jobs/1?gh_src=x")
    b = job("greenhouse:acme:1", board="gh", url="https://boards.greenhouse.io/acme/jobs/1")
    [(primary, alts)] = identity.group([a, b])
    assert primary.source == "greenhouse" and alts[0].source == "remotive"


def test_remote_spellings_merge_through_where():
    a = job("remotive:1", board="remotive", location="Remote (USA)", url="https://r/1")
    b = job("jobicy:2", board="jobicy", location="United States (Remote)", url="https://j/2")
    assert len(identity.group([a, b])) == 1


# ---------------------------------------------------------------- lifecycle (review item 4)
def run(records, groups, boards, hours, seen_rejected=None, allowed=True):
    return store.update(records, groups, seen_rejected or {}, boards, ts(hours), allowed)


def test_partial_read_never_closes():
    recs = {}
    j = job("greenhouse:a:1")
    run(recs, [(j, [])], {"gh:a": store.COMPLETE}, 0)
    for h in (6, 13, 20):
        run(recs, [], {"gh:a": store.PARTIAL}, h)
    rec = recs["greenhouse:a:1"]
    assert rec["status"] == models.OPEN
    assert rec["last_seen"] == ts(0) and rec["last_checked"] == ts(20)


def test_failed_read_moves_nothing():
    recs = {}
    run(recs, [(job("greenhouse:a:1"), [])], {"gh:a": store.COMPLETE}, 0)
    run(recs, [], {"gh:a": store.FAILED}, 6)
    rec = recs["greenhouse:a:1"]
    assert rec["last_checked"] == ts(0) and rec["status"] == models.OPEN


def test_stale_after_days_unseen_then_archived():
    recs = {}
    run(recs, [(job("remoteok:1", board="remoteok"), [])], {"remoteok": store.PARTIAL}, 0)
    run(recs, [], {"remoteok": store.PARTIAL}, 24 * 5)
    assert recs["remoteok:1"]["status"] == models.STALE
    assert store.visible(recs)[0]["display_flags"][-1] == "Unverified"
    out = run(recs, [], {"remoteok": store.PARTIAL}, 24 * 31)
    assert "remoteok:1" not in recs and out["archived"][0]["ended_as"] == "stale"


def test_reappearing_missing_role_reopens():
    recs = {}
    j = job("greenhouse:a:1")
    run(recs, [(j, [])], {"gh:a": store.COMPLETE}, 0)
    run(recs, [], {"gh:a": store.COMPLETE}, 6)
    assert recs[j.id]["status"] == models.MISSING
    out = run(recs, [(job("greenhouse:a:1"), [])], {"gh:a": store.COMPLETE}, 12)
    assert recs[j.id]["status"] == models.OPEN and out["reopened"] == 1
    assert recs[j.id]["first_seen"] == ts(0)


def test_seen_but_failing_rules_is_filtered_then_archived():
    recs = {}
    run(recs, [(job("greenhouse:a:1"), [])], {"gh:a": store.COMPLETE}, 0)
    run(recs, [], {"gh:a": store.COMPLETE}, 6, seen_rejected={"greenhouse:a:1": "full-time"})
    assert recs["greenhouse:a:1"]["status"] == models.FILTERED
    assert store.visible(recs) == []
    out = run(recs, [], {"gh:a": store.COMPLETE}, 24 * 22)
    assert out["archived"][0]["ended_as"] == models.FILTERED


def test_repost_on_new_source_joins_existing_record():
    recs = {}
    run(recs, [(job("greenhouse:a:1", requisition_id="R5"), [])], {"gh:a": store.COMPLETE}, 0)
    run(recs, [(job("smartrecruiters:a:9", board="sr:a", requisition_id="R5"), [])],
        {"gh:a": store.COMPLETE, "sr:a": store.COMPLETE}, 6)
    assert list(recs) == ["greenhouse:a:1"]
    assert recs["greenhouse:a:1"]["source_job_ids"] == ["greenhouse:a:1", "smartrecruiters:a:9"]


# ---------------------------------------------------------------- alerts (review item 9)
class FakeSender:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, content=None, embeds=None):
        self.sent.append((content, embeds or []))
        return self.ok


def rec(key, fit=models.FIT_CONFIRMED, queue=models.LISTED, **k):
    return {"key": key, "status": models.OPEN, "title": key, "company": "Acme", "queue": queue,
            "schedule_fit": fit, "where": "Remote", "first_seen": ts(0), "url": "https://x",
            "evidence": {"hours": "15 hours per week."}, **k}


NOON_ET = datetime(2026, 10, 21, 16, tzinfo=UTC)
EARLY_ET = datetime(2026, 10, 22, 9, tzinfo=UTC)   # 5am Eastern


def test_first_run_seeds_without_backlog(tmp_path):
    s = FakeSender()
    out = notify.run([rec("a"), rec("b")], tmp_path / "n.json", NOON_ET, s)
    assert out["seeded"] and len(s.sent) == 1 and "is live" in s.sent[0][0]


def test_new_confirmed_sent_once_even_on_retry(tmp_path):
    path = tmp_path / "n.json"
    notify.run([rec("a")], path, NOON_ET, FakeSender())
    s = FakeSender()
    rows = [rec("a"), rec("b")]
    notify.run(rows, path, EARLY_ET, s)
    notify.run(rows, path, EARLY_ET, s)       # retried run
    immediate = [e for c, e in s.sent if c == "New confirmed fit"]
    assert len(immediate) == 1 and immediate[0][0]["title"].startswith("b")


def test_possible_fits_wait_for_digest(tmp_path):
    path = tmp_path / "n.json"
    notify.run([rec("a")], path, NOON_ET, FakeSender())
    s = FakeSender()
    rows = [rec("a"), rec("p", fit=models.FIT_POSSIBLE), rec("r", fit=models.FIT_UNKNOWN,
                                                              queue=models.REVIEW)]
    notify.run(rows, path, EARLY_ET, s)
    assert s.sent == []                       # 5am ET: no digest yet, nothing confirmed
    notify.run(rows, path, EARLY_ET + timedelta(hours=4), s)
    assert len(s.sent) == 1 and "1 new on the list, 1 new to review" in s.sent[0][0]
    notify.run(rows, path, EARLY_ET + timedelta(hours=8), s)
    assert len(s.sent) == 1                   # one digest per day


def test_material_change_goes_in_digest(tmp_path):
    path = tmp_path / "n.json"
    notify.run([rec("a")], path, NOON_ET, FakeSender())
    s = FakeSender()
    notify.run([rec("a", hours="10/wk", salary="$30/hr")], path, EARLY_ET + timedelta(hours=4), s)
    assert "1 changed" in s.sent[0][0] and s.sent[0][1][0]["title"].startswith("[changed]")


def test_failed_send_is_retried_next_run(tmp_path):
    path = tmp_path / "n.json"
    notify.run([rec("a")], path, NOON_ET, FakeSender())
    notify.run([rec("a"), rec("b")], path, EARLY_ET, FakeSender(ok=False))
    s = FakeSender()
    notify.run([rec("a"), rec("b")], path, EARLY_ET, s)
    assert len(s.sent) == 1


def test_no_webhook_means_off(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert notify.run([rec("a")], tmp_path / "n.json") == {"enabled": False}
    assert not (tmp_path / "n.json").exists()


# ---------------------------------------------------------------- cache (review item 7)
def test_cache_signature_and_ttl(tmp_path):
    c = DescCache(tmp_path / "c.json.gz", ttl_days=7)
    j = job("greenhouse:a:1", updated_sig="v1")
    c.put(j, "text one")
    c.save()
    c2 = DescCache(tmp_path / "c.json.gz")
    assert c2.get(job("greenhouse:a:1", updated_sig="v1")) == "text one"
    assert c2.get(job("greenhouse:a:1", updated_sig="v2")) is None   # posting edited
    w = job("workday:t:/x")
    c2.put(w, "wd text")
    assert c2.get(w) == "wd text"
    c2.data[w.id]["at"] = "2026-01-01T00:00:00Z"                       # older than TTL
    assert c2.get(w) is None
