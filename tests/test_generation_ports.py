"""Covers generation_ports.py/generation_adapters.py/generation_registry.py
(docs/DESIGN.md sec 3.6) -- SDXLAdapter's own request-shape building
(previously only exercised indirectly through run_image_job in
test_api_service.py) and the registry's basic get/register/list mechanics,
same coverage shape as test_effects_registry.py covers effects/base.py's
_REGISTRY.
"""
import pytest

from server import generation_registry
from server.config import Settings
from server.generation_adapters import SDXLAdapter
from server.generation_registry import (
    available_image_generators,
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
