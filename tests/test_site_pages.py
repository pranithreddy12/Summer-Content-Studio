"""Static public pages (deploy/site) served by Caddy on the apex domain for the Meta / TikTok developer apps,
and the Caddyfile that serves them. No app code is involved: these tests keep the files, the Caddy config and the
deployment docs consistent."""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "deploy" / "site"
PAGES = ["index.html", "privacy.html", "terms.html"]
# The placeholders Lee must fill in before submitting the developer apps (documented in docs/DEPLOY.md, section 12)
PLACEHOLDERS = {"OPERATOR NAME", "CONTACT EMAIL", "DATE", "DELETION DAYS", "GOVERNING LAW"}
VOID = {"meta", "link", "br", "hr", "img", "input"}


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.tags, self.ids, self.links, self.title = [], [], [], [], [], ""
        self.lang, self._in_title = None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "html":
            self.lang = a.get("lang")
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "a":
            self.links.append(a.get("href", ""))
        for attr in ("src", "href"):
            if tag != "a" and attr in a:
                self.errors.append(f"<{tag} {attr}> loads or links something external")
        if tag == "title":
            self._in_title = True
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unbalanced </{tag}> (open: {self.stack[-3:]})")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse(name: str) -> Collect:
    c = Collect()
    c.feed((SITE / name).read_text(encoding="utf-8"))
    c.close()
    return c


def text(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def flat(name: str) -> str:
    """Page text with whitespace collapsed (phrases may wrap across source lines)."""
    return " ".join(text(name).split())


@pytest.mark.parametrize("name", PAGES)
def test_pages_are_well_formed_and_self_contained(name):
    c = parse(name)
    assert not c.errors and not c.stack, (c.errors, c.stack)
    assert c.lang == "en" and c.title.strip() and "h1" in c.tags
    assert "script" not in c.tags and "iframe" not in c.tags and "img" not in c.tags and "form" not in c.tags  # no JS, no trackers, no forms
    visible = re.sub(r"<!--.*?-->", "", text(name), flags=re.S)
    assert "http://" not in visible and "https://" not in visible                 # nothing is fetched from or pointed at another site


@pytest.mark.parametrize("name", PAGES)
def test_pages_are_not_indexed_and_responsive(name):
    html = text(name)
    assert 'name="robots" content="noindex"' in html and 'name="viewport"' in html and "prefers-color-scheme" in html


def test_pages_link_to_each_other_and_deletion_anchor_exists():
    assert "data-deletion" in parse("privacy.html").ids
    assert {"/privacy", "/terms"} <= set(parse("index.html").links)
    assert "/terms" in parse("privacy.html").links and "/" in parse("privacy.html").links
    assert "/privacy#data-deletion" in parse("terms.html").links and "/" in parse("terms.html").links


def test_placeholders_are_exactly_the_documented_ones():
    used = set()
    for name in PAGES:
        used |= set(re.findall(r"\[\[([A-Z ]+)\]\]", text(name)))
    assert used == PLACEHOLDERS, used ^ PLACEHOLDERS
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    for p in PLACEHOLDERS:
        assert f"`[[{p}]]`" in deploy, f"docs/DEPLOY.md must explain [[{p}]]"
    assert "must print nothing" in deploy                                    # the pre-publish check
    assert "[[" not in text("index.html")                                     # the landing page needs no editing


def test_privacy_policy_covers_what_the_service_really_does():
    p = flat("privacy.html")
    for provider in ("Meta", "TikTok", "Fanvue", "Anthropic", "Mistral", "DigitalOcean", "Cloudflare", "Dropbox", "Telegram",
                     "ntfy"):
        assert provider in p, provider                                         # every processor the app talks to is named
    for fact in ("encrypted", "never see or store a password", "only ever sent after the creator", "approved by a person",
                 "Data deletion instructions", "14 days"):
        assert fact in p, fact
    from app.config import Settings

    assert f"after {Settings().FAN_DATA_RETENTION_DAYS} days" in p              # Phase 3: matches the purge default
    for fact in ("only when the creator presses Send", "get no AI draft", "never the text of a fan's private message",
                 "not stored"):
        assert fact in p, fact
    backup = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    assert "-mtime +14" in backup                                              # the "14 days" in the policy matches the real backup rotation


def test_terms_cover_the_key_rules():
    t = flat("terms.html").lower()
    for fact in ("approves every post", "label ai-generated content", "not affiliated with", "&quot;as is&quot;", "governing law"):
        assert fact in t or fact.replace("&quot;", '"') in t, fact


def blocks(caddyfile: str) -> dict:
    """Top-level `site { ... }` blocks of a Caddyfile -> {address: body} (brace matching, comments stripped)."""
    src = "\n".join(line.split("#", 1)[0] for line in caddyfile.splitlines())
    out, i = {}, 0
    for m in re.finditer(r"^([^\s{]+)\s*\{", src, flags=re.M):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        out[m.group(1)] = src[m.end():j - 1]
        i = j
    return out


def test_caddy_serves_the_pages_statically_on_the_apex_and_the_app_only_on_studio():
    b = blocks((ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8"))
    assert set(b) == {"studio.itsmesummer.app", "itsmesummer.app"}, set(b)
    apex, studio = b["itsmesummer.app"], b["studio.itsmesummer.app"]
    assert "root * /var/www/summer-site" in apex and "file_server" in apex and "try_files {path}.html {path}" in apex
    assert "reverse_proxy" not in apex and "basic" not in apex.lower() and "forward_auth" not in apex   # static, not the app
    assert "nosniff" in apex and "Content-Security-Policy" in apex and "default-src 'none'" in apex
    assert "reverse_proxy 127.0.0.1:8000" in studio and "file_server" not in studio


def test_docs_and_setup_script_agree_with_the_caddyfile():
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    assert "cp /srv/summer/app/deploy/site/*.html /var/www/summer-site/" in deploy and "chmod -R a+rX /var/www/summer-site" in deploy
    for url in ("https://itsmesummer.app/privacy", "https://itsmesummer.app/privacy#data-deletion", "https://itsmesummer.app/terms"):
        assert url in deploy, url
    setup = (ROOT / "docs" / "PLATFORM_SETUP.md").read_text(encoding="utf-8")
    assert "/privacy#data-deletion" in setup and "Terms of Service URL" in setup


def test_site_files_are_shipped_with_lf_endings():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/**" in attrs and "eol=lf" in attrs                          # nested files (deploy/site) are covered too
    for f in [*PAGES, "../Caddyfile"]:
        assert b"\r\n" not in (SITE / f).read_bytes(), f


def test_the_app_itself_does_not_serve_these_pages(app, client):
    # they must stay outside the app (and its login): the app has no /privacy or /terms routes of its own
    assert client.get("/privacy").status_code == 404 and client.get("/terms").status_code == 404
