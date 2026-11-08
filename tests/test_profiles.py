"""Tests for profile API."""


def test_list_profiles_returns_summer(client):
    """GET /api/v1/profiles returns Summer."""
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    profiles = response.json()
    assert len(profiles) >= 1
    summer = next(p for p in profiles if p["slug"] == "summer")
    assert summer["name"] == "Summer"
    assert summer["is_active"] is True


def test_get_profile_has_persona_fields(client):
    """GET /api/v1/profiles/{id} includes persona fields."""
    # Get Summer's id
    response = client.get("/api/v1/profiles")
    profiles = response.json()
    summer_id = next(p["id"] for p in profiles if p["slug"] == "summer")

    response = client.get(f"/api/v1/profiles/{summer_id}")
    assert response.status_code == 200
    profile = response.json()
    assert profile["persona_description"]
    assert profile["tone_style"]
    assert isinstance(profile["communication_rules"], list)
    assert isinstance(profile["restricted_topics"], list)
    assert isinstance(profile["handles"], dict)
    assert profile["handles"]["instagram"] == "summerh_03"


def test_patch_profile_tone_persists(client):
    """PATCH /api/v1/profiles/{id} updates tone and persists."""
    response = client.get("/api/v1/profiles")
    profiles = response.json()
    summer_id = next(p["id"] for p in profiles if p["slug"] == "summer")

    new_tone = "Extra playful and witty for testing"
    response = client.patch(f"/api/v1/profiles/{summer_id}", json={"tone_style": new_tone})
    assert response.status_code == 200
    assert response.json()["tone_style"] == new_tone

    # Verify persisted
    response = client.get(f"/api/v1/profiles/{summer_id}")
    assert response.json()["tone_style"] == new_tone

    # Restore
    client.patch(f"/api/v1/profiles/{summer_id}", json={"tone_style": "Natural, conversational and engaging. Light playful teasing. Short attention-grabbing hooks when they fit. Never stiff, corporate or overly salesy."})


def test_patch_profile_empty_name_422(client):
    """PATCH with empty name returns 422."""
    response = client.get("/api/v1/profiles")
    profiles = response.json()
    summer_id = next(p["id"] for p in profiles if p["slug"] == "summer")

    response = client.patch(f"/api/v1/profiles/{summer_id}", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_profile_not_found_404(client):
    """GET /api/v1/profiles/999 returns 404 with error shape."""
    response = client.get("/api/v1/profiles/999")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "not_found"


def test_patch_profile_unknown_field_422(client):
    """PATCH with unknown field returns 422 (extra=forbid)."""
    response = client.get("/api/v1/profiles")
    profiles = response.json()
    summer_id = next(p["id"] for p in profiles if p["slug"] == "summer")

    response = client.patch(f"/api/v1/profiles/{summer_id}", json={"unknown_field": "value"})
    assert response.status_code == 422


def test_patch_profile_lists_max_items(client):
    """PATCH with too many list items returns 422."""
    response = client.get("/api/v1/profiles")
    profiles = response.json()
    summer_id = next(p["id"] for p in profiles if p["slug"] == "summer")

    response = client.patch(f"/api/v1/profiles/{summer_id}", json={
        "communication_rules": ["rule"] * 31  # max 30
    })
    assert response.status_code == 422

def test_second_profile_generates_without_code_changes(client, fake_claude):
    """PLAN.md §20 #15: a new profile is data only (one row); generation uses its persona, not Summer's."""
    from app.db import session_scope, utcnow
    from app.models import ModelProfile

    with session_scope() as db:
        p = ModelProfile(slug="nova-dod15", name="Nova", handles={}, persona_description="Nova is a desert hiker.",
                         tone_style="dry and calm", communication_rules=["Keep it short"],
                         restricted_topics=["Politics"], style_notes="", cta_guidelines="", is_active=True,
                         created_at=utcnow(), updated_at=utcnow())
        db.add(p)
        db.flush()
        pid = p.id
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": pid, "platform": "instagram", "content_type": "reel", "campaign": "halloween"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["model_profile_id"] == pid
    assert "Nova is a desert hiker." in fake_claude.last_system and "dry and calm" in fake_claude.last_system
    summer = client.get("/api/v1/profiles/1").json()
    assert summer["persona_description"] not in fake_claude.last_system


# ---- Fanvue fan rules (PLAN_PHASE3 H5): editable in the API and on the Profile page --------

def test_fan_rules_api_save_validate_clear(client):
    rules = {"never_say": ["discount", "free"], "escalate_topics": ["custom video"], "notes": "Keep it light."}
    r = client.patch("/api/v1/profiles/1", json={"fan_rules": rules})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/profiles/1").json()["fan_rules"] == rules
    for bad in ({"never_say": ["x"] * 51}, {"never_say": ["y" * 101]}, {"escalate_topics": [""]},
                {"notes": "z" * 1001}, {"price": 5}, {"never_say": "free"}):
        assert client.patch("/api/v1/profiles/1", json={"fan_rules": bad}).status_code == 422, bad
    assert client.get("/api/v1/profiles/1").json()["fan_rules"] == rules     # bad input changed nothing
    assert client.patch("/api/v1/profiles/1", json={"fan_rules": None}).status_code == 200
    assert client.get("/api/v1/profiles/1").json()["fan_rules"] is None


def test_fan_rules_form_saves_and_drive_the_inbox(client):
    from app.db import session_scope
    from app.models import ModelProfile
    from app.services.fan_inbox import draft_problem, needs_escalation

    form = {"name": "Summer", "persona_description": "p", "tone_style": "t", "communication_rules": "",
            "restricted_topics": "", "style_notes": "", "cta_guidelines": "", "is_active": "true",
            "fan_never_say": "discount\n\n  free trial  \n", "fan_escalate_topics": "custom video\n<script>x</script>",
            "fan_notes": "Be sweet."}
    r = client.post("/profiles/1", data=form, follow_redirects=False)
    assert r.status_code == 303 and "kind=error" not in r.headers["location"]
    with session_scope() as db:
        rules = db.get(ModelProfile, 1).fan_rules
    assert rules == {"never_say": ["discount", "free trial"], "escalate_topics": ["custom video", "<script>x</script>"],
                     "notes": "Be sweet."}
    # the saved rules take effect: Lee's topic escalates, Lee's word blocks a draft
    assert needs_escalation("can you make me a Custom Video?", rules) == "mentions 'custom video'"
    assert needs_escalation("hi babe", rules) is None
    assert draft_problem("here is a free trial for you", rules) == "draft used a never-say word"
    page = client.get("/profiles/1").text
    assert "custom video" in page and "<script>x</script>" not in page and "&lt;script&gt;" in page
    # too many words is reported, not saved
    r = client.post("/profiles/1", data={**form, "fan_never_say": "\n".join(f"w{i}" for i in range(51))},
                    follow_redirects=False)
    assert "kind=error" in r.headers["location"]
    with session_scope() as db:
        assert db.get(ModelProfile, 1).fan_rules["never_say"] == ["discount", "free trial"]
