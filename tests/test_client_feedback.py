from unittest.mock import AsyncMock, MagicMock

import pytest

from mem0.client.main import AsyncMemoryClient, MemoryClient


def _build_memory_client(response_payload=None):
    client = MemoryClient.__new__(MemoryClient)
    client.user_email = "user@example.com"
    client.client = MagicMock()
    response = client.client.post.return_value
    response.json.return_value = response_payload or {"message": "Feedback recorded"}
    response.raise_for_status.return_value = None
    return client


def test_memory_client_feedback_posts_feedback_payload():
    client = _build_memory_client()

    result = client.feedback("mem_1", feedback="positive", feedback_reason="accurate")

    assert result == {"message": "Feedback recorded"}
    client.client.post.assert_called_once_with(
        "/v1/feedback/",
        json={"memory_id": "mem_1", "feedback": "POSITIVE", "feedback_reason": "accurate"},
    )


@pytest.mark.asyncio
async def test_async_memory_client_feedback_posts_feedback_payload():
    client = AsyncMemoryClient.__new__(AsyncMemoryClient)
    client.user_email = "user@example.com"
    client.async_client = MagicMock()

    response = MagicMock()
    response.json.return_value = {"message": "Feedback recorded"}
    response.raise_for_status.return_value = None
    client.async_client.post = AsyncMock(return_value=response)

    result = await client.feedback("mem_1", feedback="negative", feedback_reason="outdated")

    assert result == {"message": "Feedback recorded"}
    client.async_client.post.assert_awaited_once_with(
        "/v1/feedback/",
        json={"memory_id": "mem_1", "feedback": "NEGATIVE", "feedback_reason": "outdated"},
    )
