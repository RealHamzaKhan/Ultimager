"""Tests for multimodal (vision) support in the domain judge."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

from app.services.agents.domain_judge import judge_checkpoint, JUDGE_SYSTEM_PROMPT


def _make_image(source: str = "page_1") -> dict:
    # 1x1 transparent PNG in base64
    return {
        "base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "media_type": "image/png",
        "source": source,
    }


class TestJudgeSystemPrompt:
    def test_system_prompt_mentions_visual_evidence(self):
        """System prompt must instruct the judge to handle visual evidence."""
        assert "[VISUAL]" in JUDGE_SYSTEM_PROMPT or "visual" in JUDGE_SYSTEM_PROMPT.lower()


class TestJudgeCheckpointMultimodal:
    """Tests for judge_checkpoint with image content."""

    @pytest.mark.asyncio
    async def test_with_images_sends_multimodal_message(self):
        """When images provided, API receives a list content (multimodal), not a string."""
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 75, "reasoning": "Good", '
                '"evidence_quote": "[VISUAL] Student drew a correct diagram", '
                '"source_file": "diagram.png", "confidence": "high"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            result = await judge_checkpoint(
                checkpoint_id="cp1",
                criterion="Draw a circuit diagram",
                points_max=5.0,
                pass_description="Correct diagram with all components",
                fail_description="No diagram or wrong components",
                submission_content="See attached image.",
                title="Electronics Lab",
                submission_images=[_make_image("page_1")],
            )

        assert len(captured_messages) == 1
        user_msg = captured_messages[0][1]  # index 1 = user message
        # User content must be a LIST (multimodal), not a string
        assert isinstance(user_msg["content"], list), (
            "With images, user message content must be a list (multimodal), not a string"
        )
        # Must contain at least one image_url entry
        types = [part.get("type") for part in user_msg["content"]]
        assert "image_url" in types, "No image_url found in multimodal message"

    @pytest.mark.asyncio
    async def test_without_images_sends_string_message(self):
        """Without images, API receives plain string content (existing behaviour)."""
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 100, "reasoning": "Correct", '
                '"evidence_quote": "x = 1", "source_file": "sol.py", "confidence": "high"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            result = await judge_checkpoint(
                checkpoint_id="cp1",
                criterion="Assign x=1",
                points_max=1.0,
                pass_description="x is assigned 1",
                fail_description="x is not assigned",
                submission_content="x = 1",
                title="Test",
                submission_images=None,
            )

        user_msg = captured_messages[0][1]
        assert isinstance(user_msg["content"], str), (
            "Without images, user message content must be a plain string"
        )

    @pytest.mark.asyncio
    async def test_empty_list_images_sends_string_message(self):
        """submission_images=[] (empty list, not None) also uses plain string path."""
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 100, "reasoning": "ok", '
                '"evidence_quote": "x=1", "source_file": "sol.py", "confidence": "high"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            await judge_checkpoint(
                checkpoint_id="cp1",
                criterion="test",
                points_max=1.0,
                pass_description="",
                fail_description="",
                submission_content="x=1",
                submission_images=[],   # empty list, not None
            )

        user_msg = captured_messages[0][1]
        assert isinstance(user_msg["content"], str), (
            "Empty list submission_images must use plain string path (same as None)"
        )

    @pytest.mark.asyncio
    async def test_images_capped_at_max(self):
        """More than MAX_IMAGES_PER_REQUEST images are silently capped."""
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 50, "reasoning": "ok", '
                '"evidence_quote": "[VISUAL] see image", "source_file": "x.png", "confidence": "medium"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        many_images = [_make_image(f"page_{i}") for i in range(20)]

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            await judge_checkpoint(
                checkpoint_id="cp1",
                criterion="test",
                points_max=1.0,
                pass_description="",
                fail_description="",
                submission_content="",
                submission_images=many_images,
            )

        user_content = captured_messages[0][1]["content"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        from app.config import NVIDIA_MAX_IMAGES_PER_REQUEST
        assert len(image_parts) <= NVIDIA_MAX_IMAGES_PER_REQUEST, (
            f"Sent {len(image_parts)} images but cap is {NVIDIA_MAX_IMAGES_PER_REQUEST}"
        )

    @pytest.mark.asyncio
    async def test_visual_evidence_returned(self):
        """Judge returns [VISUAL] evidence quote for image submissions."""
        def fake_create(**kwargs):
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 100, "reasoning": "Diagram is correct", '
                '"evidence_quote": "[VISUAL] Student drew a BST with root 5, left child 3, right child 7", '
                '"source_file": "diagram.png", "confidence": "high"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            result = await judge_checkpoint(
                checkpoint_id="cp1",
                criterion="Draw BST",
                points_max=5.0,
                pass_description="Correct BST",
                fail_description="Missing BST",
                submission_content="",
                submission_images=[_make_image()],
            )

        assert result.evidence_quote.startswith("[VISUAL]")
        assert result.score_percent == 100

    @pytest.mark.asyncio
    async def test_image_url_format_is_correct(self):
        """Image URL must be data URI with correct media type."""
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = (
                '{"score_percent": 75, "reasoning": "ok", '
                '"evidence_quote": "[VISUAL] diagram", "source_file": "f.png", "confidence": "high"}'
            )
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create
        img = _make_image()

        with patch("app.services.agents.domain_judge._get_nvidia_client", return_value=mock_client):
            await judge_checkpoint(
                checkpoint_id="cp1", criterion="test", points_max=1.0,
                pass_description="", fail_description="",
                submission_content="", submission_images=[img],
            )

        user_content = captured_messages[0][1]["content"]
        image_part = next(p for p in user_content if p.get("type") == "image_url")
        url = image_part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,"), f"Unexpected URL format: {url[:50]}"
        assert image_part["image_url"]["detail"] == "high"
