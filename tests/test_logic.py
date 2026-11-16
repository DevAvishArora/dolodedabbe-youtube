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
    statuses = {v["id"]: v["status"] for v in state.all_videos()}
    assert statuses == {50: "awaiting_approval", 51: "awaiting_dish"}
    assert fake.joined == []


def test_length_command(world):
    hu.handle(owner("/length 30"))
    assert state.load_settings()["raw_seconds"] == 30
    hu.handle(owner("/length 5"))
    assert state.load_settings()["raw_seconds"] == 30
