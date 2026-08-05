"""Covers generation_ports.py/generation_adapters.py/generation_registry.py
(docs/DESIGN.md sec 3.6) -- each adapter's own request/response-shape
building (previously only exercised indirectly through service.py's job
functions in test_api_service.py) and the registries' basic
get/register/list mechanics, same coverage shape as test_effects_registry.py
covers effects/base.py's _REGISTRY.
"""
import json

import pytest

from server import generation_adapters, generation_registry
from server.config import Settings
from server.generation_adapters import ACEStepAdapter, Lyria3Adapter, SDXLAdapter, StableAudioAdapter
from server.generation_registry import (
    available_image_generators,
    available_music_remix_generators,
    get_image_generator,
    register_image_generator,
)


@pytest.mark.anyio
async def test_sdxl_adapter_sends_plain_prompt_as_json_free_multipart():
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = kwargs["data"]
        captured["files"] = kwargs["files"]

        class _Resp:
            content = b"fake-png-bytes"

        return _Resp()

    adapter = SDXLAdapter(Settings(image_generation_url="http://img.invalid"), call_service=fake_call)
    result = await adapter.generate("a lofi bedroom at sunset")

    assert result == b"fake-png-bytes"
    assert captured["method"] == "POST"
    assert captured["path"] == "/generate"
    assert captured["data"] == {"prompt": "a lofi bedroom at sunset"}
    assert captured["files"] is None


@pytest.mark.anyio
async def test_sdxl_adapter_switches_to_img2img_when_reference_bytes_given():
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        captured["data"] = kwargs["data"]
        captured["files"] = kwargs["files"]

        class _Resp:
            content = b"fake-png-bytes"

        return _Resp()

    adapter = SDXLAdapter(Settings(image_generation_url="http://img.invalid"), call_service=fake_call)
    await adapter.generate(
        "a lofi bedroom at sunset",
        reference_image_bytes=b"fake-reference-bytes",
        reference_image_filename="ref.png",
        strength=0.4,
    )

    assert captured["data"] == {"prompt": "a lofi bedroom at sunset", "strength": 0.4}
    assert captured["files"] == {"image": ("ref.png", b"fake-reference-bytes")}


def test_registry_register_get_and_list_roundtrip():
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b""

    fake = FakeGenerator()
    register_image_generator("test-fake", fake)
    try:
        assert get_image_generator("test-fake") is fake
        assert "test-fake" in available_image_generators()
    finally:
        del generation_registry._IMAGE_GENERATORS["test-fake"]


def test_registry_raises_clear_error_for_unknown_name():
    with pytest.raises(KeyError, match="No image generator registered as 'nope'"):
        get_image_generator("nope")


def _ace_response(payload):
    """Minimal stand-in for the httpx.Response that call_optional_service returns."""
    class _Resp:
        content = b"audio-bytes"

        def json(self):
            return payload

    return _Resp()


@pytest.mark.anyio
async def test_acestep_adapter_reports_remote_failure(monkeypatch):
    """ACE-Step status == 2 means it gave up -- surface that as a
    RuntimeError rather than polling until the timeout. Moved here from
    test_api_service.py's old run_music_job tests once the polling loop
    itself moved into this adapter."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        return _ace_response({"data": [{"status": 2, "result": None}]})

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    with pytest.raises(RuntimeError, match="failure"):
        await adapter.generate("p", lyrics="", duration=10.0, thinking=False)


@pytest.mark.anyio
async def test_acestep_adapter_times_out_when_never_ready(monkeypatch):
    """status == 0 forever: the loop must give up rather than hang."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_MAX_ATTEMPTS", 3)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        return _ace_response({"data": [{"status": 0, "result": None}]})

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    with pytest.raises(RuntimeError, match="timed out"):
        await adapter.generate("p", lyrics="", duration=10.0, thinking=False)


@pytest.mark.anyio
async def test_acestep_adapter_downloads_audio_on_success(monkeypatch):
    """The happy path -- pins the response-envelope parsing (data[0].result
    is a JSON *string* holding a list), also verified against a live
    ACE-Step server (see docs/experiments/)."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})  # the audio download

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    result = await adapter.generate("p", lyrics="", duration=10.0, thinking=False)
    assert result == b"audio-bytes"


@pytest.mark.anyio
async def test_acestep_adapter_instrumental_sends_marker_not_submitted_lyrics(monkeypatch):
    """instrumental=True must send ACE-Step's own instrumental marker as the
    lyrics field over the wire, regardless of what lyrics text was supplied
    -- an empty (or any other) lyrics string does not make ACE-Step's real
    server omit vocals on its own; only this exact marker does (see this
    adapter's own docstring). run_music_job's own provenance records the
    submitted lyrics, not this marker -- see test_api_service.py."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    sent_lyrics = []

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            sent_lyrics.append(kwargs["json"]["lyrics"])
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})  # the audio download

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    await adapter.generate(
        "p", lyrics="some lyrics I typed", duration=10.0, thinking=False, instrumental=True,
    )

    assert sent_lyrics == ["[Instrumental]"]


@pytest.mark.anyio
async def test_stable_audio_adapter_sends_prompt_and_duration():
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs["json"]

        class _Resp:
            content = b"sfx-bytes"

        return _Resp()

    adapter = StableAudioAdapter(Settings(sound_effects_url="http://sfx.invalid"), call_service=fake_call)
    result = await adapter.generate("gentle wind chimes", duration=8.0)

    assert result == b"sfx-bytes"
    assert captured["method"] == "POST"
    assert captured["path"] == "/generate"
    assert captured["json"] == {"prompt": "gentle wind chimes", "duration": 8.0}


@pytest.mark.anyio
async def test_acestep_adapter_remix_cover_sends_task_type_and_strength_via_multipart(monkeypatch):
    """cover needs src_audio (multipart, not JSON -- same reason SDXLAdapter's
    img2img branch is) plus audio_cover_strength; no repaint-only fields
    should leak in."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            captured["data"] = kwargs["data"]
            captured["files"] = kwargs["files"]
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})  # the audio download

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    result = await adapter.remix(
        "make it jazzier", task_type="cover", src_audio_bytes=b"source-bytes", cover_strength=0.4,
    )

    assert result == b"audio-bytes"
    assert captured["data"]["task_type"] == "cover"
    assert captured["data"]["audio_cover_strength"] == 0.4
    assert "repainting_start" not in captured["data"]
    assert captured["files"] == {"src_audio": ("source.wav", b"source-bytes")}
    assert "reference_audio" not in captured["files"]


@pytest.mark.anyio
async def test_acestep_adapter_remix_repaint_sends_start_end_via_multipart(monkeypatch):
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            captured["data"] = kwargs["data"]
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    await adapter.remix(
        "regenerate the bridge", task_type="repaint", src_audio_bytes=b"source-bytes",
        repainting_start=10.0, repainting_end=20.0,
    )

    assert captured["data"]["task_type"] == "repaint"
    assert captured["data"]["repainting_start"] == 10.0
    assert captured["data"]["repainting_end"] == 20.0
    assert "audio_cover_strength" not in captured["data"]


@pytest.mark.anyio
async def test_acestep_adapter_remix_text2music_with_reference_audio_only(monkeypatch):
    """Style transfer: task_type stays text2music, only reference_audio is
    sent (no src_audio), independent of task_type per the port's own
    docstring."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    captured = {}

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            captured["data"] = kwargs["data"]
            captured["files"] = kwargs["files"]
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    await adapter.remix("a dreamy synth piece", task_type="text2music", reference_audio_bytes=b"ref-bytes")

    assert captured["data"]["task_type"] == "text2music"
    assert captured["files"] == {"reference_audio": ("reference.wav", b"ref-bytes")}
    assert "src_audio" not in captured["files"]


@pytest.mark.anyio
async def test_acestep_adapter_remix_reports_remote_failure(monkeypatch):
    """remix() reuses generate()'s _poll_and_download -- the failure path
    (status == 2) must still work after that refactor."""
    monkeypatch.setattr(generation_adapters, "MUSIC_POLL_INTERVAL_SECONDS", 0)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        return _ace_response({"data": [{"status": 2, "result": None}]})

    adapter = ACEStepAdapter(Settings(acestep_url="http://acestep.invalid"), call_service=fake_call)
    with pytest.raises(RuntimeError, match="failure"):
        await adapter.remix("p", task_type="cover", src_audio_bytes=b"source-bytes")


@pytest.mark.anyio
async def test_lyria3_adapter_remix_raises_not_implemented():
    """Lyria 3 has no source/reference-audio-conditioning mode at all --
    remix() must raise rather than silently returning an unrelated
    text2music result. The real guard in the UI/API is supports_remix
    (below); this is the defensive backstop."""
    adapter = Lyria3Adapter(Settings(gemini_api_key="fake-key"))
    with pytest.raises(NotImplementedError):
        await adapter.remix("p", task_type="cover", src_audio_bytes=b"x")


def test_available_music_remix_generators_filters_by_supports_remix():
    class RemixCapableFake:
        supports_remix = True

        async def generate(self, prompt, **kwargs):
            return b""

        async def remix(self, prompt, **kwargs):
            return b""

    class RemixIncapableFake:
        supports_remix = False

        async def generate(self, prompt, **kwargs):
            return b""

    generation_registry.register_music_generator("test-fake-remix-capable", RemixCapableFake())
    generation_registry.register_music_generator("test-fake-remix-incapable", RemixIncapableFake())
    try:
        remixable = available_music_remix_generators()
        assert "test-fake-remix-capable" in remixable
        assert "test-fake-remix-incapable" not in remixable
    finally:
        del generation_registry._MUSIC_GENERATORS["test-fake-remix-capable"]
        del generation_registry._MUSIC_GENERATORS["test-fake-remix-incapable"]


def test_music_registry_register_get_and_list_roundtrip():
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b""

    fake = FakeGenerator()
    generation_registry.register_music_generator("test-fake-music", fake)
    try:
        assert generation_registry.get_music_generator("test-fake-music") is fake
        assert "test-fake-music" in generation_registry.available_music_generators()
    finally:
        del generation_registry._MUSIC_GENERATORS["test-fake-music"]


def test_sound_effect_registry_register_get_and_list_roundtrip():
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b""

    fake = FakeGenerator()
    generation_registry.register_sound_effect_generator("test-fake-sfx", fake)
    try:
        assert generation_registry.get_sound_effect_generator("test-fake-sfx") is fake
        assert "test-fake-sfx" in generation_registry.available_sound_effect_generators()
    finally:
        del generation_registry._SOUND_EFFECT_GENERATORS["test-fake-sfx"]
