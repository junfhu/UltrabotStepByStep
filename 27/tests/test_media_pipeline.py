# tests/test_media_pipeline.py
"""媒体管道模块的测试。"""

import pytest
from pathlib import Path

from ultrabot.media.fetch import _is_safe_url
from ultrabot.media.store import MediaStore
from ultrabot.media.image_ops import get_image_info


class TestSSRFProtection:
    def test_blocks_localhost(self):
        assert _is_safe_url("http://localhost/secret") is False
        assert _is_safe_url("http://127.0.0.1:8080/api") is False

    def test_blocks_private_ranges(self):
        assert _is_safe_url("http://10.0.0.1/internal") is False
        assert _is_safe_url("http://192.168.1.1/admin") is False
        assert _is_safe_url("http://172.16.0.1/data") is False

    def test_allows_public_urls(self):
        assert _is_safe_url("https://example.com/image.png") is True
        assert _is_safe_url("https://cdn.github.com/file.pdf") is True

    def test_blocks_non_http(self):
        assert _is_safe_url("ftp://example.com/file") is False
        assert _is_safe_url("file:///etc/passwd") is False


class TestMediaStore:
    @pytest.fixture
    def store(self, tmp_path):
        return MediaStore(base_dir=tmp_path / "media", ttl_seconds=10)

    def test_save_and_get(self, store):
        result = store.save(b"Hello World", "test.txt", "text/plain")
        assert result["size"] == 11
        assert result["content_type"] == "text/plain"
        assert store.get(result["id"]) is not None

    def test_save_detects_mime(self, store):
        # PNG 魔术字节
        png_header = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        result = store.save(png_header, "image.png")
        assert result["content_type"] == "image/png"

        # JPEG 魔术字节
        jpeg_header = b'\xff\xd8\xff' + b'\x00' * 100
        result = store.save(jpeg_header, "photo.jpg")
        assert result["content_type"] == "image/jpeg"

        # PDF 魔术字节
        pdf_header = b'%PDF-1.4' + b'\x00' * 100
        result = store.save(pdf_header, "doc.pdf")
        assert result["content_type"] == "application/pdf"

    def test_size_limit(self, store):
        store.max_size_bytes = 100
        with pytest.raises(ValueError, match="too large"):
            store.save(b"x" * 200, "big.bin")

    def test_delete(self, store):
        result = store.save(b"temp", "temp.txt")
        assert store.delete(result["id"]) is True
        assert store.get(result["id"]) is None
        assert store.delete("nonexistent") is False

    def test_list_files(self, store):
        store.save(b"file1", "a.txt")
        store.save(b"file2", "b.txt")
        files = store.list_files()
        assert len(files) == 2

    def test_sanitize_filename(self):
        assert MediaStore._sanitize_filename("normal.txt") == "normal.txt"
        assert MediaStore._sanitize_filename("bad file!@#.txt") == "bad_file___.txt"
        assert MediaStore._sanitize_filename("") == "file"


class TestImageOps:
    def test_get_image_info_no_pillow(self):
        # 如果 Pillow 未安装，应返回错误字典
        info = get_image_info(b"not an image")
        # 返回格式信息或错误 — 两者都有效
        assert isinstance(info, dict)


class TestMimeDetection:
    def test_magic_bytes(self):
        assert MediaStore._detect_mime(b'\x89PNG\r\n\x1a\n', "x") == "image/png"
        assert MediaStore._detect_mime(b'\xff\xd8\xff', "x") == "image/jpeg"
        assert MediaStore._detect_mime(b'GIF89a', "x") == "image/gif"
        assert MediaStore._detect_mime(b'%PDF-1.5', "x") == "application/pdf"

    def test_extension_fallback(self):
        assert MediaStore._detect_mime(b'unknown', "file.mp3") == "audio/mpeg"
        assert MediaStore._detect_mime(b'unknown', "file.json") == "application/json"
        assert MediaStore._detect_mime(b'unknown', "file.xyz") == "application/octet-stream"
