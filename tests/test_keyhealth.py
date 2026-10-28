"""A key that expires or runs out shows up in the dashboard and the terminal, and clears once it works again."""
import httpx
from fastapi.testclient import TestClient

from leadhunter import keyhealth
from leadhunter.checklist import checklist
from leadhunter.terminal import IO, print_checklist
from leadhunter.web import create_app


def test_explain_reads_status_and_error_text():
    assert "rejected" in keyhealth.explain(401)
    assert "rejected" in keyhealth.explain(400, '{"message": "Please pass a valid API key"}')      # Gemini
    assert "rejected" in keyhealth.explain(422, '{"code":"SUBSCRIPTION_TOKEN_INVALID"}')          # Brave
    assert "limit" in keyhealth.explain(429) and "credits" in keyhealth.explain(402)
    assert keyhealth.explain(500) is None and keyhealth.explain(400, "bad query") is None          # not the key's fault


def test_bad_key_is_shown_everywhere_then_clears(agent, cfg, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "old-key")
    keyhealth.report_http(cfg, "ai", 400, "Please pass a valid API key")
    keyhealth.report_http(cfg, "search", 503)                                                     # outage: ignored
    assert [k for k, _, _ in keyhealth.problems(cfg)] == ["ai"]

    html = TestClient(create_app(cfg, agent)).get("/dashboard").text
    assert 'class="flash alert"' in html and "AI writing was rejected" in html and 'href="/setup?step=4">Fix it' in html
    start = TestClient(create_app(cfg, agent)).get("/start").text
    assert "Key problem" in start

    item = next(i for i in checklist(cfg) if i["key"] == "ai")
    assert item["done"] is False and "rejected" in item["problem"]
    print_checklist(IO(), cfg, full=False)
    assert "KEY PROBLEM" in capsys.readouterr().out

    keyhealth.report_http(cfg, "ai", 200)                                                         # new key works
    assert keyhealth.problems(cfg) == []
    assert 'class="flash alert"' not in TestClient(create_app(cfg, agent)).get("/dashboard").text


def test_check_all_tests_each_key_without_leaking_it(cfg, monkeypatch):
    for k, v in {"BRAVE_API_KEY": "b", "HUNTER_API_KEY": "h", "TELEGRAM_BOT_TOKEN": "1:t", "GMAIL_APP_PASSWORD": ""}.items():
        monkeypatch.setenv(k, v)
    cfg["search"]["provider"] = "brave"
    cfg["email_finder"]["hunter"] = True
    cfg["ai"]["provider"] = "none"

    def fake(method, url, **kw):
        req = httpx.Request(method, url)
        if "brave" in url:
            return httpx.Response(422, text='{"code":"SUBSCRIPTION_TOKEN_INVALID"}', request=req)
        if "hunter" in url:   # free searches used up for the month
            return httpx.Response(200, json={"data": {"requests": {"searches": {"used": 25, "available": 25}}}}, request=req)
        raise httpx.ConnectError("offline", request=req)
    monkeypatch.setattr(keyhealth.httpx, "request", fake)
    res = keyhealth.check_all(cfg)
    assert "rejected" in res["search"] and "credits" in res["hunter"] and "couldn't reach" in res["telegram"]
    saved = {k for k, _, _ in keyhealth.problems(cfg)}
    assert saved == {"search", "hunter"}                                     # being offline isn't blamed on the key
    assert "1:t" not in keyhealth._file(cfg).read_text()


def test_telegram_chat_id_found_for_you_and_bot_number_refused(agent, cfg, tmp_path, monkeypatch):
    tok = "123456789:AAHfakefakefakefakefakefakefake12"
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(k, "")
    from leadhunter import web
    monkeypatch.setattr(web, "telegram_chat_id", lambda t: "")            # user hasn't messaged the bot yet
    c = TestClient(create_app(cfg, agent, env_path=str(tmp_path / ".env")))
    r = c.post("/setup/4", data={"tg_token": tok, "tg_chat": "123456789"})  # the bot's own number
    assert "send your bot any message" in r.text
    assert "TELEGRAM_CHAT_ID" not in (tmp_path / ".env").read_text()      # never saved
    monkeypatch.setattr(web, "telegram_chat_id", lambda t: "555")          # after saying hi to the bot
    c.post("/setup/4", data={})
    assert "TELEGRAM_CHAT_ID=555" in (tmp_path / ".env").read_text()


def test_telegram_chat_id_reads_private_messages(monkeypatch):
    def fake_get(url, timeout):
        return httpx.Response(200, json={"result": [{"message": {"chat": {"id": -100, "type": "group"}}},
                                                    {"message": {"chat": {"id": 777, "type": "private"}}}]},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(keyhealth.httpx, "get", fake_get)
    assert keyhealth.telegram_chat_id("1:x") == "777"


def test_retired_ai_model_is_reported(cfg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cfg["ai"].update(provider="gemini", model="gemini-old-flash")
    from leadhunter.ai import AI

    def fake_get(url, headers, timeout):
        return httpx.Response(200, json={"data": [{"id": "models/gemini-flash-latest"}]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(keyhealth.httpx, "get", fake_get)
    assert "gemini-old-flash is gone" in keyhealth._ai_model(AI(cfg))
    cfg["ai"]["model"] = "gemini-flash-latest"
    assert keyhealth._ai_model(AI(cfg))[0] == 200


def test_ai_busy_model_falls_back_once(cfg, monkeypatch):
    from leadhunter import ai as ai_mod
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cfg["ai"].update(provider="gemini", model="gemini-flash-latest")
    models = []

    def fake_post(url, json, headers, timeout):
        models.append(json["model"])
        req = httpx.Request("POST", url)
        if json["model"] == "gemini-flash-latest":
            raise httpx.ReadTimeout("busy", request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)
    monkeypatch.setattr(ai_mod.httpx, "post", fake_post)
    assert ai_mod.AI(cfg).chat("s", "u") == "ok" and models == ["gemini-flash-latest", "gemini-flash-lite-latest"]
