"""Unit tests for generation/__init__.py (Gemini image + Lyria 3 music
client) and server/generation_adapters.py's GeminiAdapter/Lyria3Adapter.
Mocks google.genai's Client and generate_music's own injected
`post_interaction` entirely -- these must never make a real network call;
the real contract (model names, MIME types, base64 response, io.BytesIO
requirement for reference images, the `response_format` mime_type bug, the
steps[]-scanning response shape) was confirmed separately against the live
API and is recorded in generation/__init__.py's own docstring and
docs/experiments/2026-07-31-gemini-adapter.md /
docs/experiments/2026-08-01-lyria3-adapter.md, not re-verified here.
"""

import base64
import io

import cv2
import numpy as np
import pytest

import generation
from server.config import Settings
from server.generation_adapters import GeminiAdapter, Lyria3Adapter


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
async def test_generate_image_text_to_image_sends_plain_string_input_and_returns_png(
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(
        generation.genai, "Client", lambda api_key: _FakeClient(captured)
    )

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
    monkeypatch.setattr(
        generation.genai, "Client", lambda api_key: _FakeClient(captured)
    )

    await generation.generate_image(
        "fake-key",
        "make it lofi",
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

    monkeypatch.setattr(
        generation.genai,
        "Client",
        lambda api_key: type("C", (), {"aio": _EmptyAio()})(),
    )

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


class _FakeAudioResp:
    def __init__(self, status_code, json_data, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


def _audio_step(mime_type="audio/mpeg", data=b"fake-mp3-bytes"):
    return {
        "type": "model_output",
        "content": [
            {
                "type": "audio",
                "mime_type": mime_type,
                "data": base64.b64encode(data).decode("ascii"),
            }
        ],
    }


@pytest.mark.anyio
async def test_generate_music_omits_mime_type_and_scans_steps_for_audio():
    """Pins the two real findings from docs/experiments/2026-08-01-lyria3-adapter.md:
    `response_format` must NOT include `mime_type` (the real API 400s on it),
    and the audio comes back inside `steps[].content[]`, not a top-level field."""
    captured = {}

    async def fake_post(api_key, payload):
        captured["api_key"] = api_key
        captured["payload"] = payload
        return _FakeAudioResp(
            200,
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "<instrumental>"}],
                    },
                    _audio_step(),
                ]
            },
        )

    result = await generation.generate_music(
        "fake-key", "cinematic piano", post_interaction=fake_post
    )

    assert captured["api_key"] == "fake-key"
    assert captured["payload"]["model"] == generation.MUSIC_MODEL
    assert captured["payload"]["response_format"] == {"type": "audio"}
    assert "mime_type" not in captured["payload"]["response_format"]
    assert result == b"fake-mp3-bytes"


@pytest.mark.anyio
async def test_generate_music_folds_lyrics_instrumental_duration_into_prompt_text():
    """Lyria 3 has no separate fields for any of these (confirmed against the
    real API/docs) -- all three must show up as text inside `input`."""
    captured = {}

    async def fake_post(api_key, payload):
        captured["payload"] = payload
        return _FakeAudioResp(200, {"steps": [_audio_step()]})

    await generation.generate_music(
        "fake-key",
        "a ballad",
        lyrics="[Verse]\nHello world",
        duration=90.0,
        instrumental=True,
        post_interaction=fake_post,
    )

    input_text = captured["payload"]["input"]
    assert "a ballad" in input_text
    assert "[Verse]\nHello world" in input_text
    assert "instrumental" in input_text.lower()
    assert "90" in input_text


@pytest.mark.anyio
async def test_generate_music_raises_clearly_on_non_200():
    async def fake_post(api_key, payload):
        return _FakeAudioResp(400, {}, text='{"error": {"message": "boom"}}')

    with pytest.raises(RuntimeError, match="400"):
        await generation.generate_music(
            "fake-key", "prompt", post_interaction=fake_post
        )


@pytest.mark.anyio
async def test_generate_music_raises_clearly_when_no_audio_step():
    async def fake_post(api_key, payload):
        return _FakeAudioResp(
            200,
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "oops"}],
                    }
                ]
            },
        )

    with pytest.raises(RuntimeError, match="no audio"):
        await generation.generate_music(
            "fake-key", "prompt", post_interaction=fake_post
        )


@pytest.mark.anyio
async def test_lyria3_adapter_delegates_to_generation_module(monkeypatch):
    """Lyria3Adapter is a thin pass-through, same shape as GeminiAdapter's own
    test -- confirms it wires settings.gemini_api_key through and that
    `thinking` is accepted but never forwarded (Lyria 3 has no equivalent)."""
    captured = {}

    async def fake_generate_music(api_key, prompt, **kwargs):
        captured["api_key"] = api_key
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return b"fake-mp3-bytes"

    monkeypatch.setattr(generation, "generate_music", fake_generate_music)

    adapter = Lyria3Adapter(Settings(gemini_api_key="fake-key"))
    result = await adapter.generate(
        "cinematic piano",
        lyrics="[Verse]\nHi",
        duration=90.0,
        thinking=True,
        instrumental=True,
    )

    assert result == b"fake-mp3-bytes"
    assert captured["api_key"] == "fake-key"
    assert captured["prompt"] == "cinematic piano"
    assert captured["kwargs"] == {
        "lyrics": "[Verse]\nHi",
        "duration": 90.0,
        "instrumental": True,
    }
    assert "thinking" not in captured["kwargs"]
