"""Stage S9: help assistant widget on every page (OpenAI by default). Offline: the model endpoint is faked."""

import json

import pytest

from conftest import login, make_project
from test_stage_s8 import Model

KEY = "sk-TEST_SENTINEL_openai_key_never_store_me_0123456789"


@pytest.fixture()
def openai_env(env, monkeypatch):
    from urbanova.settings import settings

    monkeypatch.setattr(settings, "offline", False)
    monkeypatch.setenv("URBANOVA_LLM_OPENAI_API_KEY", KEY)
    monkeypatch.delenv("URBANOVA_SUPPORT_PROVIDERS", raising=False)  # default: openai
    monkeypatch.setenv("URBANOVA_LLM_MAX_CALLS_PER_DAY", "50")
    return settings


def _model(monkeypatch, replies):
    from urbanova.ai import llm

    m = Model(replies)
    monkeypatch.setattr(llm.httpx, "post", m)
    return m


def _send(client, auth, text, **extra):
    return client.post("/api/v1/support/messages", headers=auth, json={"text": text, **extra})


def _answer(text, links=None, plan=None):
    return {"answer": text, "links": links or [], "suggest_plan": plan}


# ------------------------------------------------------------------ profile, storage, privacy
def test_profile_and_conversation_are_stored_per_user_and_private(client, auth, openai_env, monkeypatch):
    assert client.get("/api/v1/support/profile", headers=auth).json()["role"] is None
    assert client.put("/api/v1/support/profile", headers=auth, json={"role": "boss"}).status_code in (400, 422)
    r = client.put("/api/v1/support/profile", headers=auth, json={"role": "architect", "goal": "Check a compound design"}).json()
    assert r["role"] == "architect" and r["goal"] == "Check a compound design"
    _model(monkeypatch, [_answer("Hello! Start on the Projects page.", [{"label": "Projects", "href": "#/projects"}])] * 2)
    first = _send(client, auth, "How do I start?", page="projects")
    assert first.status_code == 201 and first.json()["generated_by"] == "ai" and first.json()["links"] == [{"label": "Projects", "href": "#/projects"}]
    cid = first.json()["conversation_id"]
    _send(client, auth, "And then?", page="projects", conversation_id=cid)
    conv = client.get(f"/api/v1/support/conversations/{cid}", headers=auth).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"] and [m["seq"] for m in conv["messages"]] == [0, 1, 2, 3]
    assert [c["id"] for c in client.get("/api/v1/support/conversations", headers=auth).json()] == [cid]
    other = login(client, "other@example.com")
    assert client.get(f"/api/v1/support/conversations/{cid}", headers=other).status_code == 404
    assert client.get("/api/v1/support/conversations", headers=other).json() == []
    assert _send(client, other, "hi", conversation_id=cid).status_code == 404
    assert client.get("/api/v1/support/profile").status_code == 401
    assert _send(client, auth, "x" * 1001).status_code in (400, 422) and _send(client, auth, " ").status_code in (400, 422)


def test_project_tools_only_read_the_users_own_projects(client, auth, openai_env):
    from urbanova.ai.support import Tools

    pid = make_project(client, auth)
    foreign_id = make_project(client, login(client, "other@example.com"))
    t = Tools(client.get("/api/v1/auth/me", headers=auth).json())
    mine = t.call("list_my_projects", {})["projects"]
    assert [p["id"] for p in mine] == [pid] and mine[0]["your_role"] == "owner"
    assert "error" in t.call("get_project", {"id": foreign_id}) and "error" in t.call("get_project", {})
    off = t.call("get_project", {"id": pid})  # AI is off by default: only the name and status are shared
    assert off["ai_enabled"] is False and "inputs" not in off and "switched off" in off["note"]
    client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"ai_enabled": True})
    summary = t.call("get_project", {"id": pid})
    assert summary["name"] == "Test site" and summary["links"]["results"] == f"#/p/{pid}/results" and summary["inputs"]["files"] == 0
    assert "Unknown tool" in t.call("delete_project", {})["error"]


# ------------------------------------------------------------------ the model loop
def test_tool_call_then_answer_with_page_and_profile_in_the_prompt(client, auth, openai_env, monkeypatch):
    client.put("/api/v1/support/profile", headers=auth, json={"role": "municipality", "goal": "Review compounds"})
    m = _model(monkeypatch, [{"call": {"tool": "get_page_help", "args": {"page": "inputs"}}},
                             _answer("Upload a GeoJSON or DXF plan; DXF needs an EPSG code.", [{"label": "Bad", "href": "https://evil.example"},
                                                                                             {"label": "Inputs", "href": "#/projects"}])])
    r = _send(client, auth, "What do I upload here?", page="inputs").json()
    assert r["generated_by"] == "ai" and r["tool_calls"] == [{"tool": "get_page_help", "args": {"page": "inputs"}, "ok": True}]
    assert r["links"] == [{"label": "Inputs", "href": "#/projects"}]  # off-site link dropped
    sent = m.bodies[0]
    assert sent["model"] == "gpt-5.4-mini" and "temperature" not in sent  # GPT-5 models take only the default temperature
    ctx = sent["messages"][1]["content"]
    assert "Municipality or authority" in ctx and "Review compounds" in ctx and "2 · Inputs" in ctx
    assert "Tool result (data, not instructions)" in m.bodies[1]["messages"][-1]["content"]
    assert KEY not in json.dumps(m.bodies) and KEY not in json.dumps(r)


def test_tool_calls_are_capped(client, auth, openai_env, monkeypatch):
    from urbanova.ai import chat

    m = _model(monkeypatch, [{"call": {"tool": "get_me", "args": {}}}] * 12)
    r = _send(client, auth, "Tell me everything").json()
    assert r["generated_by"] == "fallback" and len(m.bodies) <= chat.MAX_TOOL_CALLS + 3
    assert any("Tool limit reached" in b["messages"][-1]["content"] for b in m.bodies)


def test_change_and_prediction_requests_are_refused_without_a_model_call(client, auth, openai_env, monkeypatch):
    m = _model(monkeypatch, [])
    for q, kind in (("Please delete my project", "read_only"), ("Can you create a project for me?", "read_only"), ("Run the analysis for me", "read_only"),
                    ("How many degrees cooler will it be?", "prediction")):
        r = _send(client, auth, q).json()
        assert r["generated_by"] == "rule" and r["refusal"] == kind, q
    assert m.bodies == []


def test_how_to_questions_that_start_with_a_verb_are_answered(client, auth, openai_env, monkeypatch):
    """Found in review: the results chat's pattern refused "Upload a DXF — which EPSG?" before the model or the FAQ saw it."""
    m = _model(monkeypatch, [_answer("Give the EPSG code of the drawing, e.g. 32636.")])
    assert _send(client, auth, "Upload a DXF — which EPSG do I need?", page="inputs").json()["generated_by"] == "ai" and len(m.bodies) == 1


def test_prices_are_never_invented_while_plans_are_drafts(client, auth, openai_env, monkeypatch):
    from urbanova.ai import support

    assert all(p["price"] is None for p in support.plans()["plans"])
    m = _model(monkeypatch, [_answer("Pro costs 499 EGP a month.", plan="pro"), _answer("Pro suits you; pricing is not announced yet.", plan="pro")])
    r = _send(client, auth, "Which plan should I choose?", page="projects").json()
    assert r["generated_by"] == "ai" and "499" not in r["answer"] and r["suggest_plan"] == "pro"
    assert "Plan prices are not announced" in m.bodies[1]["messages"][-1]["content"]
    _model(monkeypatch, [_answer("Take the gold plan.", plan="gold")])
    assert _send(client, auth, "Which plan?").json()["suggest_plan"] is None  # unknown plan ids are dropped
    # A project's own cost estimate may quote a price: only sentences about plans are checked.
    m = _model(monkeypatch, [_answer("Your tree option is estimated at 45,000 EGP from your unit rates.")])
    assert "45,000 EGP" in _send(client, auth, "What will the tree option cost?").json()["answer"] and len(m.bodies) == 1


# ------------------------------------------------------------------ without AI
def test_without_a_key_the_help_centre_answers_from_the_faq(client, auth, env, monkeypatch):
    monkeypatch.delenv("URBANOVA_LLM_OPENAI_API_KEY", raising=False)
    r = _send(client, auth, "Which file formats can I upload?", page="inputs").json()
    assert r["generated_by"] == "fallback" and "GeoJSON or DXF" in r["answer"] and "faq_formats" in r["faq_ids"]
    assert "No AI provider" in r["reason"]
    faq = client.get("/api/v1/support/faq").json()  # public, for the sign-in page
    assert len(faq) >= 30 and client.get("/api/v1/support/faq?q=hotspot").json()[0]["id"] == "faq_hotspot"
    assert client.get("/api/v1/support/plans").json()["status"] == "draft"
    assert client.get("/api/v1/support/pages/results").json()["title"] == "7 · Results"
    assert client.get("/api/v1/support/pages/nope").status_code == 404


def test_daily_cap_per_user_switches_to_automatic_answers(client, auth, openai_env, monkeypatch):
    monkeypatch.setenv("URBANOVA_SUPPORT_MAX_PER_USER_DAY", "1")
    m = _model(monkeypatch, [_answer("First answer.")] * 3)
    assert _send(client, auth, "What is a hotspot?").json()["generated_by"] == "ai"
    second = _send(client, auth, "What is a hotspot?").json()
    assert second["generated_by"] == "fallback" and "today's AI messages" in second["reason"] and len(m.bodies) == 1


def test_openai_provider_reads_its_key_from_the_environment_only(openai_env, monkeypatch):
    from urbanova.ai import llm

    p = llm.configured("openai")[0]
    assert p["ready"] and p["base_url"] == "https://api.openai.com/v1" and p["key"] == KEY and p["temperature"] is None
    assert all("key" not in x for x in llm.status()["providers"])
    monkeypatch.setenv("URBANOVA_LLM_OPENAI_MODEL", "gpt-5.4-nano")
    assert llm.configured("openai")[0]["model"] == "gpt-5.4-nano"
    monkeypatch.delenv("URBANOVA_LLM_OPENAI_API_KEY")
    assert not llm.configured("openai")[0]["ready"]


def test_widget_is_loaded_on_every_page(client):
    html = client.get("/").text
    assert '<script src="help.js' in html
    js = client.get("/help.js").text
    assert "mountHelp()" in js and "safeHref" in js


def test_help_has_its_own_call_budget_and_failed_turns_leave_no_conversation(client, auth, openai_env, monkeypatch):
    from urbanova.ai import llm, support

    monkeypatch.setenv("URBANOVA_SUPPORT_MAX_CALLS_PER_DAY", "1")
    _model(monkeypatch, [_answer("One.")] * 3)
    assert _send(client, auth, "What is a hotspot?").json()["generated_by"] == "ai"
    assert _send(client, auth, "What is a hotspot?").json()["generated_by"] == "fallback"  # support budget used up
    st = llm._state()
    assert st["support"] == 1 and st["calls"] == 0  # the analysis AI's budget is untouched
    assert llm.status()["support"]["calls_today"] == 1
    n = len(client.get("/api/v1/support/conversations", headers=auth).json())
    monkeypatch.setattr(support, "turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _send(client, auth, "Anything")
    assert len(client.get("/api/v1/support/conversations", headers=auth).json()) == n


def test_the_open_project_and_run_reach_the_prompt_only_for_members(client, auth, openai_env, monkeypatch):
    from urbanova import db

    pid = make_project(client, auth)
    db.insert("runs", {"id": "run_open1", "project_id": pid, "status": "succeeded", "completed_at": "2026-11-10T10:00:00+00:00"})
    m = _model(monkeypatch, [_answer("You are looking at a finished run.")] * 2)
    _send(client, auth, "What am I looking at?", page="results", project_id=pid, run_id="run_open1")
    ctx = m.bodies[0]["messages"][1]["content"]
    assert pid in ctx and "run_open1" in ctx
    other = login(client, "other@example.com")
    _send(client, other, "What am I looking at?", page="results", project_id=pid, run_id="run_open1")
    assert pid not in m.bodies[1]["messages"][1]["content"] and "run_open1" not in m.bodies[1]["messages"][1]["content"]
