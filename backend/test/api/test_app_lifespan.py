"""Exercise startup and shutdown through the ASGI application."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main
from storage import mysql_client, redis_client


def test_resume_reads_redis_on_the_startup_loop(monkeypatch):
    lifecycle = []

    class Redis:
        async def ping(self):
            self.loop = asyncio.get_running_loop()
            lifecycle.append("ping")
            return True

        async def get(self, key):
            assert asyncio.get_running_loop() is self.loop
            lifecycle.append("get")
            return None

        async def aclose(self):
            assert asyncio.get_running_loop() is self.loop
            lifecycle.append("close")

    monkeypatch.setattr(redis_client, "_redis_client", None)
    monkeypatch.setattr(redis_client.aioredis, "Redis", lambda **kwargs: Redis())
    monkeypatch.setattr(main, "_check_mysql_connection", AsyncMock())
    monkeypatch.setattr(main, "_init_knowledge_base", AsyncMock())

    with TestClient(main.app) as client:
        response = client.get("/api/resume/content", params={"session_id": "lifespan-test"})
        assert response.status_code == 404
        assert response.json() == {"detail": "会话不存在"}

    assert lifecycle == ["ping", "get", "close"]
    assert redis_client._redis_client is None


def test_resources_close_when_startup_fails(monkeypatch):
    class Pool:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

        wait_closed = AsyncMock()

    pool = Pool()
    redis = AsyncMock()
    monkeypatch.setattr(mysql_client, "_pool", pool)
    monkeypatch.setattr(redis_client, "_redis_client", redis)
    monkeypatch.setattr(main, "_check_mysql_connection", AsyncMock())
    monkeypatch.setattr(main, "_check_redis_connection", AsyncMock(side_effect=RuntimeError("redis startup")))

    with pytest.raises(RuntimeError, match="redis startup"), TestClient(main.app):
        pass

    assert pool.close_calls == 1
    pool.wait_closed.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    assert mysql_client._pool is None
    assert redis_client._redis_client is None
