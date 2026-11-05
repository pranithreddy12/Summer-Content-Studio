"""Phase 3 C5: tracking links, earnings/subscriber report, weekly digest, worker stages, /fanvue page.
Fanvue is a MockTransport fake; nothing leaves the process."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.models import NotificationLog, TrackingLink
from app.services import AppError
from app.services import fanvue_report as rep
from tests.test_fan_inbox import live, no_sleep  # noqa: F401 (fixtures)
from tests.test_publish_live import NOON, NoHttp, db

MONDAY_0930 = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)   # 09:30 London (GMT in November)


@pytest.fixture(autouse=True)
def clear_cache():
    rep._cache.clear()
    yield
    rep._cache.clear()


def link(uuid, name, platform="instagram", clicks=3, subs=1, net=1234):
    return {"uuid": uuid, "name": name, "linkUrl": f"https://fanvue.com/its_me_summer?tl={uuid}",
            "externalSocialPlatform": platform, "clicks": clicks,
            "engagement": {"acquiredSubscribers": subs, "acquiredFollowers": 2},
            "earnings": {"totalGross": net * 1.25, "totalNet": net} if net is not None else None}


class Fake:
    def __init__(self, links=None):
        self.links = list(links or [])
        self.calls = []
        self.earn = {"totals": {"thisMonth": {"net": 5000, "gross": 6250}, "allTime": {"net": 9, "gross": 9}},
                     "breakdownBySource": {"subs": {"gross": 2500, "net": 2000}, "tips": {"gross": 625, "net": 500},
                                           "messages": {"gross": 0, "net": 0}}}
        self.subs = [{"date": "2026-10-27", "total": 40, "newSubscribersCount": 3, "cancelledSubscribersCount": 1},
                     {"date": "2026-10-28", "total": 42, "newSubscribersCount": 2, "cancelledSubscribersCount": 0}]

    def client(self):
        def handler(req):
            if req.url.host == "auth.fanvue.com":                 # worker refresh of the 1-hour token
                return httpx.Response(200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 3600})
            body = json.loads(req.content) if req.content and "json" in req.headers.get("content-type", "") else None
            self.calls.append(SimpleNamespace(method=req.method, path=req.url.path, params=dict(req.url.params), json=body))
            p = req.url.path
            if p == "/v1/tracking-links" and req.method == "POST":
                item = link(f"L{len(self.links) + 1}", body["name"], body["externalSocialPlatform"], 0, 0, None)
                self.links.append(item)
                return httpx.Response(201, json=item)
            if p == "/v1/tracking-links":
                return httpx.Response(200, json={"data": self.links, "nextCursor": None})
            if p == "/v1/insights/earnings/summary":
                return httpx.Response(200, json=self.earn)
            if p == "/v1/insights/subscribers":
                return httpx.Response(200, json={"data": self.subs, "nextCursor": None})
            return httpx.Response(599, json={"error": f"unexpected {req.method} {p}"})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def posts(self):
        return [c for c in self.calls if c.method == "POST"]


# ---- links -------------------------------------------------------------------------

def test_money():
    assert rep.money(123456) == "$1,234.56" and rep.money(None) == "-" and rep.money(0) == "$0.00"


def test_bio_links_created_once_and_never_duplicated(live):
    fake = Fake()
    out = rep.ensure_bio_links(db(), fake.client(), NOON)
    assert sorted(l.name for l in out) == ["summer-instagram-bio", "summer-tiktok-bio"]
    assert [c.json for c in fake.posts()] == [{"name": "summer-instagram-bio", "externalSocialPlatform": "instagram"},
                                              {"name": "summer-tiktok-bio", "externalSocialPlatform": "tiktok"}]
    rep.ensure_bio_links(db(), NoHttp(), NOON)                                  # second call: no HTTP at all


def test_bio_links_reuse_existing_fanvue_links_after_a_lost_database(live):
    fake = Fake(links=[link("OLD1", "summer-instagram-bio")])
    rep.ensure_bio_links(db(), fake.client(), NOON)
    assert [c.json["name"] for c in fake.posts()] == ["summer-tiktok-bio"]      # only the missing one
    assert db().query(TrackingLink).count() == 2


@pytest.mark.parametrize("name,platform,code", [("A", "instagram", "validation_error"),
                                                ("Has Space", "instagram", "validation_error"),
                                                ("ok-name", "myspace", "validation_error")])
def test_campaign_link_validation(live, name, platform, code):
    with pytest.raises(AppError) as e:
        rep.create_link(db(), NoHttp(), name, platform, NOON)
    assert e.value.code == code


def test_campaign_link_create_duplicate_and_cap(live):
    fake = Fake()
    created = rep.create_link(db(), fake.client(), "Halloween-2027-IG", "instagram", NOON)
    assert created.name == "halloween-2027-ig" and created.link_url.startswith("https://fanvue.com/")
    with pytest.raises(AppError) as e:
        rep.create_link(db(), fake.client(), "halloween-2027-ig", "instagram", NOON)
    assert e.value.code == "duplicate"
    d = db()
    for i in range(rep.MAX_LINKS - 1):
        d.add(TrackingLink(model_profile_id=1, fanvue_uuid=f"x{i}", name=f"l-{i:03d}", platform="other", link_url="u"))
    d.commit()
    with pytest.raises(AppError) as e:
        rep.create_link(db(), fake.client(), "one-too-many", "instagram", NOON)
    assert e.value.code == "limit_reached" and len(fake.posts()) == 1


def test_links_need_live_mode(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    with pytest.raises(AppError) as e:
        rep.create_link(db(), NoHttp(), "valid-name", "instagram", NOON)
    assert e.value.code == "mode_not_live"


def test_sync_links_updates_counts_and_includes_links_made_in_fanvue(live):
    rep.ensure_bio_links(db(), Fake().client(), NOON)
    fake = Fake(links=[link("L1", "summer-instagram-bio", clicks=40, subs=5, net=9900),
                       link("L2", "summer-tiktok-bio", "tiktok", clicks=7, subs=0, net=None),
                       link("APP1", "made-in-app", "reddit")])
    assert rep.sync_links(db(), fake.client(), NOON + timedelta(hours=6)) == {"links": 3}
    by = {l.name: l for l in db().query(TrackingLink).all()}
    assert (by["summer-instagram-bio"].clicks, by["summer-instagram-bio"].acquired_subscribers,
            by["summer-instagram-bio"].earnings_net_cents) == (40, 5, 9900)
    assert by["summer-tiktok-bio"].earnings_net_cents is None and "made-in-app" in by


# ---- report ------------------------------------------------------------------------

def test_summary_period_sums_and_cache(live, env):
    env(APP_TIMEZONE="Europe/London")
    fake = Fake()
    s = rep.summary(db(), fake.client(), 7, MONDAY_0930)
    assert (s["net"], s["gross"], s["this_month_net"]) == (2500, 3125, 5000)
    assert [x["source"] for x in s["sources"]] == ["subs", "messages", "tips"]
    assert (s["new_subscribers"], s["cancelled_subscribers"]) == (5, 1)
    earn = [c for c in fake.calls if c.path.endswith("/summary")][0]
    assert earn.params["startDate"] == "2026-10-26T00:00:00+00:00" and earn.params["endDate"] == "2026-11-02T00:00:00+00:00"
    assert earn.params["timezone"] == "Europe/London"
    rep.summary(db(), NoHttp(), 7, MONDAY_0930 + timedelta(minutes=9))         # cached: no HTTP
    rep.summary(db(), fake.client(), 7, MONDAY_0930 + timedelta(minutes=11))
    assert len([c for c in fake.calls if c.path.endswith("/summary")]) == 2


def test_summary_uses_local_midnight_in_summer_time(live, env):
    env(APP_TIMEZONE="Europe/London")
    fake = Fake()
    rep.summary(db(), fake.client(), 30, datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc))
    earn = [c for c in fake.calls if c.path.endswith("/summary")][0]
    assert earn.params["endDate"] == "2026-07-10T00:00:00+01:00"


def test_summary_validation_and_modes(phase2, client):
    with pytest.raises(AppError) as e:
        rep.summary(db(), NoHttp(), 14, NOON)
    assert e.value.code == "validation_error"
    phase2(PUBLISHING_MODE="dry_run")
    with pytest.raises(AppError) as e:
        rep.summary(db(), NoHttp(), 7, NOON)
    assert e.value.code == "mode_not_live"


def test_summary_without_account(phase2, client):
    phase2(PUBLISHING_MODE="live", DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="a-long-password-123")
    with pytest.raises(AppError) as e:
        rep.summary(db(), NoHttp(), 7, NOON)
    assert e.value.code == "account_not_connected"


# ---- weekly digest -----------------------------------------------------------------

def test_digest_only_on_monday_morning_and_once_per_week(live, env, monkeypatch):
    env(APP_TIMEZONE="Europe/London", NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="secret-topic")
    sent = []
    import app.services.notifications as n

    def fake_notify(db_, event, key, title, body="", url=None, http=None):
        sent.append((key, body))
        db_.add(NotificationLog(event=event, event_key=key, provider="ntfy", success=True))
        db_.commit()
        return True
    monkeypatch.setattr(n, "notify", fake_notify)
    assert rep.weekly_digest(db(), NoHttp(), MONDAY_0930 - timedelta(hours=1)) is False    # 08:30: too early
    assert rep.weekly_digest(db(), NoHttp(), MONDAY_0930 + timedelta(days=1)) is False     # Tuesday
    rep.ensure_bio_links(db(), Fake().client(), NOON)
    d = db()
    d.query(TrackingLink).filter_by(name="summer-instagram-bio").one().acquired_subscribers = 4
    d.commit()
    assert rep.weekly_digest(db(), Fake().client(), MONDAY_0930) is True
    key, body = sent[0]
    assert key == "fanvue_weekly:2026-W45"
    assert body == ("Last 7 days: $25.00 net (subs $20.00, tips $5.00). Subscribers: +5 new, -1 cancelled. "
                    "Best link: summer-instagram-bio (4 subscribers).")
    assert rep.weekly_digest(db(), NoHttp(), MONDAY_0930 + timedelta(hours=3)) is False    # already sent this week


# ---- worker stages ------------------------------------------------------------------

def test_worker_link_and_digest_stages_are_paced(live):
    from app.services import scheduler

    rep.ensure_bio_links(db(), Fake().client(), NOON)
    fake = Fake(links=[link("L1", "summer-instagram-bio", clicks=9)])
    out = scheduler.run_once(db(), NOON, http=fake.client())
    assert out["fanvue_links"] == {"links": 1} and out["fanvue_digest"] is False          # NOON is Monday 12:00, but no push channel
    out = scheduler.run_once(db(), NOON + timedelta(minutes=30), http=fake.client())
    assert "fanvue_links" not in out and "fanvue_digest" not in out
    out = scheduler.run_once(db(), NOON + timedelta(hours=6, minutes=1), http=fake.client())
    assert "fanvue_links" in out and "fanvue_digest" in out


def test_report_stages_skip_without_a_fanvue_account(phase2, client):
    from app.services import scheduler

    phase2(PUBLISHING_MODE="live", DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="a-long-password-123")
    out = scheduler.run_once(db(), NOON, http=NoHttp())
    assert "fanvue_links" not in out and "fanvue_digest" not in out and "errors" not in out


# ---- API + page ---------------------------------------------------------------------

@pytest.fixture
def ui_http(app, live, monkeypatch):
    from app.api.dependencies import get_platform_http
    from app.models import PlatformAccount
    from app.services import accounts

    monkeypatch.setattr(accounts, "is_worker", False)
    d = db()
    d.query(PlatformAccount).filter_by(platform="fanvue").one().token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    d.commit()
    fake = Fake()
    app.dependency_overrides[get_platform_http] = lambda: fake.client()
    yield fake
    app.dependency_overrides.pop(get_platform_http, None)


def test_api_links_and_summary(live, ui_http):
    assert live.post("/api/v1/fanvue/links/bio").status_code == 200
    r = live.post("/api/v1/fanvue/links", json={"name": "promo-one", "platform": "tiktok"})
    assert r.status_code == 201 and r.json()["name"] == "promo-one"
    assert len(live.get("/api/v1/fanvue/links").json()) == 3
    s = live.get("/api/v1/fanvue/summary?days=30")
    assert s.status_code == 200 and s.json()["net"] == 2500
    assert live.get("/api/v1/fanvue/summary?days=3").status_code == 422


def test_fanvue_page_live_and_dry_run(live, ui_http):
    live.post("/fanvue/links/bio")
    page = live.get("/fanvue").text
    assert "$25.00" in page and "summer-instagram-bio" in page and "https://fanvue.com/its_me_summer?tl=L1" in page
    assert 'href="/fanvue"' in page


def test_fanvue_page_explains_when_not_live(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    page = client.get("/fanvue")
    assert page.status_code == 200 and "only contacted in live mode" in page.text
    r = client.post("/fanvue/links", data={"name": "abc-def", "platform": "instagram"}, follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]
