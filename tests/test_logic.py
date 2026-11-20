import json
from pathlib import Path

import pytest

from pipeline import handle_update as hu
from pipeline import media, research, state, writer


@pytest.mark.parametrize("caption,expected", [
    ("Paneer bhurji #final", ("Paneer bhurji", "final")),
    ("#raw Dal tadka", ("Dal tadka", "raw")),
    ("Aloo paratha", ("Aloo paratha", "final")),
    ("  Masala   dosa -  #RAW #new ", ("Masala dosa", "raw")),
    ("#raw", ("", "raw")),
    (None, ("", "final")),
])
def test_parse_caption(caption, expected):
    assert hu.parse_caption(caption) == expected


def test_parse_command():
    assert hu.parse_command("/instruct@DollaBot use Hinglish") == ("instruct", "use Hinglish")
    assert hu.parse_command("/status") == ("status", "")


def test_has_video():
    assert hu.has_video({"video": {"file_id": "x"}})
    assert hu.has_video({"document": {"mime_type": "video/mp4"}})
    assert not hu.has_video({"document": {"mime_type": "application/pdf"}})
    assert not hu.has_video({"text": "hi"})


def test_rank_prefers_outliers_and_skips_tiny_channels():
    vids = [
        {"title": "big", "views": 1_000_000, "subs": 5_000_000},
        {"title": "breakout", "views": 200_000, "subs": 2_000},
        {"title": "tiny", "views": 50_000, "subs": 10},
    ]
    ranked = research.rank(vids)
    assert [v["title"] for v in ranked] == ["breakout", "big"]
    assert ranked[0]["ratio"] == 100.0


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE", tmp_path)
    monkeypatch.setattr(state, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(state, "SETTINGS", tmp_path / "settings.json")
    assert state.load_settings()["paused"] is False
    state.save_video({"id": 12, "dish": "Dal", "draft_msg_id": 99, "status": "new"})
    state.save_video({"id": 3, "dish": "Roti", "status": "new"})
    assert [v["id"] for v in state.all_videos()] == [3, 12]
    assert state.find_video("draft_msg_id", 99)["dish"] == "Dal"
    assert state.load_video(404) is None


def test_plan_join_fits_untouched():
    assert media.plan_join([10, 20], 40) == (1.0, [(0.0, 10), (0.0, 20)])


def test_plan_join_speeds_up_then_trims():
    speed, segs = media.plan_join([40, 40], 40)
    assert speed == 2.0 and segs == [(0.0, 40.0), (0.0, 40.0)]
    speed, segs = media.plan_join([200, 200], 40)
    assert speed == 4.0
    assert segs == [(60.0, 80.0), (60.0, 80.0)]
    assert sum(length for _, length in segs) / speed == pytest.approx(40)


def test_join_args_audio_and_logo():
    a = media.join_args([Path("a"), Path("b")], Path("o.mp4"), 1.0,
                        [(0, 5), (0, 5)], True, Path("logo.png"))
    assert a.count("-i") == 3 and "[aout]" in a
    fc = a[a.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in fc and "overlay" in fc
    b = media.join_args([Path("a")], Path("o.mp4"), 2.0, [(0, 5)], False, None)
    assert "[aout]" not in b and "concat=n=1:v=1:a=0" in b[b.index("-filter_complex") + 1]


def test_draft_prompt_includes_rules_and_change():
    p = writer.draft_prompt("Dal", "- x", ["Use Hinglish"], "shorter", {"title": "Old"})
    assert "Use Hinglish" in p and "shorter" in p and "Old" in p


class FakeTG:
    def __init__(self):
        self.sent = []

    def to_owner(self, text, reply_to=None):
        self.sent.append(("owner", text))
        return 500 + len(self.sent)

    def send(self, chat, text, reply_to=None):
        self.sent.append((chat, text))
        return 700 + len(self.sent)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE", tmp_path)
    monkeypatch.setattr(state, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(state, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setenv("TG_CHAT_ID", "-100123")
    monkeypatch.setenv("TG_OWNER_ID", "42")
    fake = FakeTG()
    monkeypatch.setattr(hu, "tg", fake)
    monkeypatch.setattr(hu.research, "top_videos", lambda dish: [])
    monkeypatch.setattr(hu.writer, "write_draft", lambda *a, **k: {
        "title": f"T:{a[0]}:{a[3] if len(a) > 3 else ''}", "title_alternatives": ["A", "B"],
        "hook_text": "h", "description": "d #shorts", "tags": ["x"]})
    return fake


def clip(msg_id, caption=None):
    return {"message": {"message_id": msg_id, "chat": {"id": -100123, "type": "supergroup"},
                        "from": {"id": 7}, "video": {"file_id": "f"}, "caption": caption}}


def owner(text, reply_to=None):
    m = {"message_id": 900, "chat": {"id": 42, "type": "private"},
         "from": {"id": 42}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"message": m}


def test_full_flow_final_clip(world):
    hu.handle(clip(10, "Dal tadka #final"))
    v = state.load_video(10)
    assert v["status"] == "awaiting_approval" and "Dal tadka" in world.sent[-1][1]

    hu.handle(owner("make it Hinglish", reply_to=v["draft_msg_id"]))
    v = state.load_video(10)
    assert v["draft"]["title"] == "T:Dal tadka:make it Hinglish"

    hu.handle(owner("OK", reply_to=v["draft_msg_id"]))
    assert state.load_video(10)["status"] == "approved"
    assert world.sent[-3][1] == v["draft"]["title"]


def test_duplicate_update_is_ignored(world):
    hu.handle(clip(10, "Dal"))
    count = len(world.sent)
    hu.handle(clip(10, "Dal"))
    assert len(world.sent) == count


def test_missing_dish_is_asked_then_processed(world):
    hu.handle(clip(11))
    v = state.load_video(11)
    assert v["status"] == "awaiting_dish"
    hu.handle({"message": {"message_id": 12, "chat": {"id": -100123, "type": "supergroup"},
                           "from": {"id": 7}, "text": "Aloo gobi",
                           "reply_to_message": {"message_id": v["ask_msg_id"]}}})
    v = state.load_video(11)
    assert v["dish"] == "Aloo gobi" and v["status"] == "awaiting_approval"


def test_pause_holds_clips_and_rules_are_saved(world):
    hu.handle(owner("/pause"))
    hu.handle(clip(20, "Poha"))
    assert state.load_video(20)["status"] == "held"
    hu.handle(owner("/resume"))
    hu.handle(owner("/instruct Always use Hinglish"))
    assert state.load_settings()["instructions"] == ["Always use Hinglish"]
    hu.handle(owner("/redo 20"))
    assert state.load_video(20)["status"] == "awaiting_approval"
    hu.handle(owner("/forget 1"))
    assert state.load_settings()["instructions"] == []


def test_strangers_are_ignored(world):
    hu.handle({"message": {"message_id": 1, "chat": {"id": 555, "type": "private"},
                           "from": {"id": 555}, "text": "/pause"}})
    assert world.sent == [] and state.load_settings()["paused"] is False


def test_failure_is_reported_and_recorded(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gemini down")
    monkeypatch.setattr(hu.writer, "write_draft", boom)
    with pytest.raises(RuntimeError):
        hu.handle(clip(30, "Idli"))
    assert state.load_video(30)["status"] == "failed"
    assert "gemini down" in world.sent[-1][1]


class FakeMedia:
    def __init__(self, ids, caption):
        self.ids, self.caption, self.joined = ids, caption, []

    def fetch_album(self, chat, message_id):
        return {"ids": self.ids, "caption": self.caption}

    def download_many(self, chat, ids):
        return [Path(f"/tmp/{i}") for i in ids]

    def join(self, srcs, target, name):
        self.joined.append((srcs, target))
        return Path("/tmp/out.mp4"), "note"

    def upload(self, chat, path, caption):
        pass


def album_item(msg_id, caption=None):
    u = clip(msg_id, caption)
    u["message"]["media_group_id"] = "g1"
    return u


def test_raw_album_becomes_one_video(world, monkeypatch):
    import pipeline
    fake = FakeMedia([40, 41, 42], "Dal tadka #raw")
    monkeypatch.setattr(pipeline, "media", fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "pipeline.media", fake)
    hu.handle(album_item(41))
    hu.handle(album_item(42))
    assert state.all_videos() == []
    hu.handle(album_item(40, "Dal tadka #raw"))
    vids = state.all_videos()
    assert len(vids) == 1 and vids[0]["parts"] == [40, 41, 42]
    assert vids[0]["status"] == "awaiting_approval" and vids[0]["edited"]
    assert len(fake.joined) == 1 and len(fake.joined[0][0]) == 3
    assert fake.joined[0][1] == 40


def test_final_album_stays_separate(world, monkeypatch):
    import pipeline
    fake = FakeMedia([50, 51], "Poha #final")
    monkeypatch.setattr(pipeline, "media", fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "pipeline.media", fake)
    hu.handle(album_item(50, "Poha #final"))
    hu.handle(album_item(51))
    vids = {v["id"]: v for v in state.all_videos()}
    assert {i: v["status"] for i, v in vids.items()} == {
        50: "awaiting_approval", 51: "awaiting_approval"}
    assert vids[51]["dish"] == "Poha"  # the album caption names every reel
    assert fake.joined == []


def test_length_command(world):
    hu.handle(owner("/length 30"))
    assert state.load_settings()["raw_seconds"] == 30
    hu.handle(owner("/length 5"))
    assert state.load_settings()["raw_seconds"] == 30


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        return self._data


def ok_body(obj):
    return {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True},
        {"text": "```json\n" + json.dumps(obj) + "\n```"}]}}]}


@pytest.fixture
def gem(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "main-model")
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    monkeypatch.setattr(writer.time, "sleep", lambda s: None)
    calls = []

    def install(responses):
        def fake_post(model, body):
            calls.append((model, json.loads(json.dumps(body))))
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(writer, "_post", fake_post)
    return install, calls


def test_gemini_retries_timeout_then_succeeds(gem):
    install, calls = gem
    install([writer.requests.Timeout(), FakeResp(200, ok_body({"title": "T"}))])
    assert writer.ask_json("p") == {"title": "T"}
    assert [c[0] for c in calls] == ["main-model", "main-model"]
    assert calls[0][1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_gemini_drops_thinking_on_400(gem):
    install, calls = gem
    install([FakeResp(400, text="bad thinkingLevel"), FakeResp(200, ok_body({"a": 1}))])
    assert writer.ask_json("p") == {"a": 1}
    assert "thinkingConfig" not in calls[1][1]["generationConfig"]


def test_gemini_falls_back_to_lighter_model(gem):
    install, calls = gem
    install([writer.requests.Timeout()] * 3 + [FakeResp(200, ok_body({"ok": True}))])
    assert writer.ask_json("p") == {"ok": True}
    assert calls[-1][0] == "gemini-3.5-flash-lite"


def test_gemini_gives_clear_error(gem):
    install, calls = gem
    install([FakeResp(403, text="API key not valid")] * 2)
    with pytest.raises(writer.GeminiError, match="403"):
        writer.ask_json("p")


def test_failure_sends_one_message_only(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("slow")
    monkeypatch.setattr(hu.writer, "write_draft", boom)
    with pytest.raises(RuntimeError) as info:
        hu.handle(clip(31, "Idli"))
    assert getattr(info.value, "reported", False)


def test_empty_env_value_uses_default(monkeypatch):
    from pipeline import config
    monkeypatch.setenv("GEMINI_MODEL", "")  # how GitHub passes an unset repo variable
    assert config.gemini_model() == "gemini-3.8-flash"
    monkeypatch.setenv("TG_BOT_TOKEN", "")
    with pytest.raises(RuntimeError, match="TG_BOT_TOKEN"):
        config.bot_token()


def test_gemini_timeout_after_dropping_thinking_still_retries(gem):
    install, calls = gem
    install([FakeResp(400, text="bad thinkingLevel"), writer.requests.Timeout(),
             FakeResp(200, ok_body({"ok": 1}))])
    assert writer.ask_json("p") == {"ok": 1}
    assert [c[0] for c in calls] == ["main-model"] * 3
    assert "thinkingConfig" not in calls[2][1]["generationConfig"]


def test_redo_without_dish_asks_in_group(world):
    hu.handle(owner("/pause"))
    hu.handle(clip(60))
    assert state.load_video(60)["status"] == "held"
    hu.handle(owner("/resume"))
    hu.handle(owner("/redo 60"))
    v = state.load_video(60)
    assert v["status"] == "awaiting_dish" and v["ask_msg_id"]
    assert any(chat == -100123 and "Which dish" in text for chat, text in world.sent)
    hu.handle({"message": {"message_id": 61, "chat": {"id": -100123, "type": "supergroup"},
                           "from": {"id": 7}, "text": "Upma",
                           "reply_to_message": {"message_id": v["ask_msg_id"]}}})
    assert state.load_video(60)["status"] == "awaiting_approval"


def test_ok_on_older_draft_approves_that_version(world):
    hu.handle(clip(70, "Rajma"))
    first = state.load_video(70)
    hu.handle(owner("make it Hinglish", reply_to=first["draft_msg_id"]))
    newer = state.load_video(70)
    assert newer["draft_msg_id"] != first["draft_msg_id"]
    hu.handle(owner("ok", reply_to=first["draft_msg_id"]))
    v = state.load_video(70)
    assert v["status"] == "approved"
    assert v["draft"]["title"] == first["draft"]["title"]
    assert world.sent[-3][1] == first["draft"]["title"]


def test_success_clears_old_error(world, monkeypatch):
    real = hu.writer.write_draft

    def boom(*a, **k):
        raise RuntimeError("slow")
    monkeypatch.setattr(hu.writer, "write_draft", boom)
    with pytest.raises(RuntimeError):
        hu.handle(clip(80, "Kheer"))
    monkeypatch.setattr(hu.writer, "write_draft", real)
    hu.handle(owner("/redo 80"))
    v = state.load_video(80)
    assert v["status"] == "awaiting_approval" and "error" not in v


def test_status_lists_pending_before_approved(world):
    for i in range(1, 6):
        hu.handle(clip(100 + i, f"Dish {i}"))
        hu.handle(owner("ok", reply_to=state.load_video(100 + i)["draft_msg_id"]))
    hu.handle(clip(200, "Halwa"))
    hu.handle(owner("/status"))
    text = world.sent[-1][1]
    assert text.startswith("200: Halwa - awaiting_approval")
    assert "Recently approved:" in text and "101:" not in text and "105:" in text


def test_telegram_login_happens_once_per_run(monkeypatch):
    import sys
    import types
    logins = []

    class FakeClient:
        def __init__(self, *a):
            pass

        def start(self, bot_token=None):
            logins.append(bot_token)

        def disconnect(self):
            pass

    monkeypatch.setitem(sys.modules, "telethon.sync", types.SimpleNamespace(TelegramClient=FakeClient))
    monkeypatch.setitem(sys.modules, "telethon.sessions", types.SimpleNamespace(StringSession=lambda: None))
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "h")
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setattr(media, "_CLIENT", None)
    assert media._client() is media._client()
    assert logins == ["t"]
