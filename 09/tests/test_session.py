# tests/test_session.py
import asyncio, tempfile
from pathlib import Path
from ultrabot.session.manager import Session, SessionManager


def test_session_add_and_trim():
    s = Session(session_id="test")
    # 添加一个系统提示词 — 它永远不应被修剪。
    s.add_message({"role": "system", "content": "You are helpful."})
    for i in range(20):
        s.add_message({"role": "user", "content": "x" * 400})  # 每条约 100 个 token

    assert s.token_count > 100
    removed = s.trim(max_tokens=200)
    assert removed > 0
    # 系统提示词必须存活。
    assert s.messages[0]["role"] == "system"
    assert s.token_count <= 200


def test_session_serialization():
    s = Session(session_id="round-trip")
    s.add_message({"role": "user", "content": "Hello!"})
    data = s.to_dict()
    restored = Session.from_dict(data)
    assert restored.session_id == "round-trip"
    assert len(restored.messages) == 1


def test_session_manager_persistence():
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(Path(tmp), max_sessions=5)
            session = await mgr.get_or_create("user:42")
            session.add_message({"role": "user", "content": "ping"})
            await mgr.save("user:42")

            # 模拟重启：在同一目录上创建新的 manager。
            mgr2 = SessionManager(Path(tmp))
            reloaded = await mgr2.get_or_create("user:42")
            assert len(reloaded.messages) == 1
            assert reloaded.messages[0]["content"] == "ping"

    asyncio.run(_run())


def test_session_manager_eviction():
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(Path(tmp), max_sessions=2)
            await mgr.get_or_create("a")
            await mgr.get_or_create("b")
            await mgr.get_or_create("c")  # 应该淘汰 "a"
            assert "a" not in mgr._sessions

    asyncio.run(_run())
