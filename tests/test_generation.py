"""Unit tests for generation/__init__.py (Gemini image client) and
server/generation_adapters.py's GeminiAdapter. Mocks google.genai's Client
entirely -- these must never make a real network call; the real contract
(model name, MIME types, base64 response, io.BytesIO requirement for
reference images) was confirmed separately against the live API and is
recorded in generation/__init__.py's own docstring and
docs/experiments/2026-07-31-gemini-adapter.md, not re-verified here.
"""
import base64
import io

import cv2
import numpy as np
import pytest

import generation
from server.config import Settings
from server.generation_adapters import GeminiAdapter


def _fake_jpeg_base64() -> str:
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[:, :] = (10, 20, 30)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


class _FakeOutputImage:
    def __init__(self, data):
        self.data = data


class _FakeResponse:
    def __init__(self, data):
        self.status = "completed"
        self.output_image = _FakeOutputImage(data)


class _FakeInteractions:
    def __init__(self, captured):
        self._captured = captured

    async def create(self, **kwargs):
        self._captured.update(kwargs)
        return _FakeResponse(_fake_jpeg_base64())


class _FakeAio:
    def __init__(self, captured):
        self.interactions = _FakeInteractions(captured)


class _FakeClient:
    def __init__(self, captured, api_key=None):
        self.aio = _FakeAio(captured)


@pytest.mark.anyio
async def test_generate_image_text_to_image_sends_plain_string_input_and_returns_png(monkeypatch):
    captured = {}
    monkeypatch.setattr(generation.genai, "Client", lambda api_key: _FakeClient(captured))

    result = await generation.generate_image("fake-key", "a lofi bedroom at sunset")

    assert captured["model"] == generation.MODEL
    assert captured["input"] == "a lofi bedroom at sunset"
    assert captured["response_format"] == {"type": "image", "mime_type": "image/jpeg"}
    # PNG magic bytes -- confirms the JPEG->PNG conversion actually ran,
    # not just that some bytes came back.
    assert result[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.anyio
async def test_generate_image_img2img_sends_multimodal_input_with_bytesio(monkeypatch):
    captured = {}
    monkeypatch.setattr(generation.genai, "Client", lambda api_key: _FakeClient(captured))

    await generation.generate_image(
        "fake-key", "make it lofi",
        reference_image_bytes=b"fake-reference-bytes",
        reference_image_filename="ref.png",
    )

    assert captured["input"][0] == {"type": "text", "text": "make it lofi"}
    image_part = captured["input"][1]
    assert image_part["type"] == "image"
    assert image_part["mime_type"] == "image/png"
    assert isinstance(image_part["data"], io.BytesIO)
    assert image_part["data"].getvalue() == b"fake-reference-bytes"


@pytest.mark.anyio
async def test_generate_image_raises_clearly_when_no_output_image(monkeypatch):
    class _EmptyInteractions:
        async def create(self, **kwargs):
            class _Resp:
                status = "failed"
                output_image = None

            return _Resp()

    class _EmptyAio:
        interactions = _EmptyInteractions()

    monkeypatch.setattr(generation.genai, "Client", lambda api_key: type("C", (), {"aio": _EmptyAio()})())

    with pytest.raises(RuntimeError, match="Gemini returned no image"):
        await generation.generate_image("fake-key", "a prompt")


@pytest.mark.anyio
async def test_gemini_adapter_delegates_to_generation_module(monkeypatch):
    """GeminiAdapter is a thin pass-through -- confirms it wires
    settings.gemini_api_key through and doesn't duplicate any of
    generation.generate_image's own logic."""
    captured = {}

    async def fake_generate_image(api_key, prompt, **kwargs):
        captured["api_key"] = api_key
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return b"fake-png-bytes"

    monkeypatch.setattr(generation, "generate_image", fake_generate_image)

    adapter = GeminiAdapter(Settings(gemini_api_key="fake-key"))
    result = await adapter.generate(
        "a lofi bedroom at sunset",
        reference_image_bytes=b"ref-bytes",
        reference_image_filename="ref.png",
        strength=0.9,  # accepted, ignored -- Gemini has no equivalent knob
    )

    assert result == b"fake-png-bytes"
    assert captured["api_key"] == "fake-key"
    assert captured["prompt"] == "a lofi bedroom at sunset"
    assert captured["kwargs"] == {
        "reference_image_bytes": b"ref-bytes",
        "reference_image_filename": "ref.png",
    }
