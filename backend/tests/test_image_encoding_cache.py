"""Encoding reuse must preserve pixels, input validation and actual vision calls."""
import base64
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from app.agents.director import vision
from app.agents.director.material_review import capture_references, observe_references_cached
from test_director_material_review import material_shot
from test_reference_facts import Vision, observation


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(vision, '_encoded_image_cache', vision._EncodedImageCache())


def image_bytes(color='red', mode='RGB', format='PNG'):
    image = Image.new(mode, (96, 72), color)
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def main_encoder(data, max_side):
    """Compatibility oracle: main's pre-cache JPEG algorithm/settings."""
    image = Image.open(io.BytesIO(data)).convert('RGB')
    width, height = image.size
    if max(width, height) > max_side:
        scale = max_side / float(max(width, height))
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=85, optimize=True)
    return base64.b64encode(output.getvalue()).decode('ascii')


@pytest.mark.parametrize('mode,color', [('RGB', 'red'), ('RGBA', (0, 100, 200, 80)), ('L', 128)])
def test_cached_payload_matches_main_bytes_for_each_size(monkeypatch, mode, color):
    data = image_bytes(color, mode)
    expected = {size: main_encoder(data, size) for size in (48, 768)}
    original = Image.open
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', counted)
    for _ in range(3):
        for size in (48, 768):
            assert vision.image_bytes_to_b64_jpeg(memoryview(data).tobytes(), max_side=size) == expected[size]
    assert len(calls) == 2


def test_changed_file_bytes_invalidate_even_with_same_name_size_and_timestamp(material_shot):
    _, shot, _, files = material_shot
    path = files[0]
    path.write_bytes(image_bytes('red', format='BMP'))
    before_stat = path.stat()
    old_records, old_images, old_signature = capture_references(shot)
    path.write_bytes(image_bytes('blue', format='BMP'))
    os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    assert path.stat().st_size == before_stat.st_size
    assert path.stat().st_mtime_ns == before_stat.st_mtime_ns
    records, images, signature = capture_references(shot)
    assert records[0]['content_sha256'] != old_records[0]['content_sha256']
    assert images[0] != old_images[0]
    assert signature != old_signature
    path.unlink()
    with pytest.raises(ValueError, match='missing'):
        capture_references(shot)


def test_current_metadata_is_not_cached_with_encoded_pixels(material_shot):
    _, shot, _, files = material_shot
    _, images, signature = capture_references(shot)
    path = files[0].parent/'asset.json'
    asset = json.loads(path.read_text())
    asset['notes'] = 'A new authoritative requirement.'
    path.write_text(json.dumps(asset), encoding='utf8')
    records, current_images, current_signature = capture_references(shot)
    assert records[0]['approved_notes'] == asset['notes']
    assert current_images == images
    assert current_signature != signature


@pytest.mark.asyncio
async def test_explicit_reinspection_still_calls_vision(material_shot):
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    provider = Vision(observation(), observation())
    await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    fresh_records, fresh_images, _ = capture_references(shot)
    await observe_references_cached(provider, project.id, fresh_records[:1], fresh_images[:1], lambda: None,
                                    force_reinspect=True)
    assert len(provider.calls) == 2
    assert provider.calls[0][2]['images'] == provider.calls[1][2]['images']


def test_missing_pillow_keeps_original_fallback_even_after_cache_hit(monkeypatch):
    data = image_bytes()
    assert vision.image_bytes_to_b64_jpeg(data) != base64.b64encode(data).decode('ascii')
    monkeypatch.setitem(sys.modules, 'PIL', None)
    assert vision.image_bytes_to_b64_jpeg(data) == base64.b64encode(data).decode('ascii')


def test_failed_conversion_is_not_cached(monkeypatch):
    data = image_bytes()
    expected = main_encoder(data, 512)
    original = Image.open
    calls = []
    def transient(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('Transient decoder failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', transient)
    assert vision.image_bytes_to_b64_jpeg(data) is None
    assert vision.image_bytes_to_b64_jpeg(data) == expected
    assert vision.image_bytes_to_b64_jpeg(data) == expected
    assert len(calls) == 2


@pytest.mark.parametrize('data', [b'', b'short', b'not an image' * 10])
def test_invalid_images_still_fail(data):
    assert vision.image_bytes_to_b64_jpeg(data) is None


def test_byte_budget_lru_and_oversized_bypass():
    cache = vision._EncodedImageCache(max_bytes=8, max_entries=3)
    cache.put('a', 'aaaa'); cache.put('b', 'bbbb')
    assert cache.get('a') == 'aaaa'
    cache.put('c', 'cccc')
    assert cache.get('b') is None
    cache.put('oversized', 'x' * 9)
    assert cache.get('oversized') is None
    assert cache.get('a') == 'aaaa'
    cache.put('a', 'a')
    assert cache._bytes == 5
    assert cache._bytes == sum(map(len, cache._entries.values()))


def test_entry_bound_and_concurrent_updates():
    cache = vision._EncodedImageCache(max_bytes=1000, max_entries=3)
    def update(i):
        key = i % 10
        value = str(key) * 10
        cache.put(key, value)
        assert cache.get(key) in (None, value)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(200)))
    assert len(cache._entries) <= 3
    assert cache._bytes == sum(map(len, cache._entries.values())) <= 1000
