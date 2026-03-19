"""Tests for the abstract file storage backend (CRKY-35).

Covers: LocalStorage operations, FileStorage interface contract,
path traversal prevention, singleton management, and S3Storage
initialization guard.
"""

import os

import pytest


class TestLocalStorage:
    """Test the local filesystem storage implementation."""

    @pytest.fixture
    def storage(self, tmp_path):
        from web.api.file_storage import LocalStorage

        return LocalStorage(str(tmp_path))

    def test_write_and_read(self, storage, tmp_path):
        storage.write_file("test/hello.txt", b"hello world")
        data = storage.read_file("test/hello.txt")
        assert data == b"hello world"

    def test_read_missing_raises(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.read_file("nonexistent.txt")

    def test_file_exists(self, storage):
        assert not storage.file_exists("test.txt")
        storage.write_file("test.txt", b"data")
        assert storage.file_exists("test.txt")

    def test_delete_file(self, storage):
        storage.write_file("to_delete.txt", b"data")
        assert storage.delete_file("to_delete.txt")
        assert not storage.file_exists("to_delete.txt")

    def test_delete_missing_returns_false(self, storage):
        assert not storage.delete_file("nonexistent.txt")

    def test_get_file_size(self, storage):
        storage.write_file("sized.bin", b"x" * 42)
        assert storage.get_file_size("sized.bin") == 42

    def test_get_file_size_missing(self, storage):
        assert storage.get_file_size("missing.bin") == 0

    def test_list_files(self, storage):
        storage.write_file("dir/a.txt", b"a")
        storage.write_file("dir/b.txt", b"b")
        storage.write_file("dir/sub/c.txt", b"c")
        files = storage.list_files("dir")
        assert len(files) == 3
        assert any("a.txt" in f for f in files)
        assert any("c.txt" in f for f in files)

    def test_list_files_empty(self, storage):
        assert storage.list_files("nonexistent") == []

    def test_delete_prefix(self, storage):
        storage.write_file("prefix/a.txt", b"a")
        storage.write_file("prefix/b.txt", b"b")
        count = storage.delete_prefix("prefix")
        assert count == 2
        assert not storage.file_exists("prefix/a.txt")

    def test_delete_prefix_empty(self, storage):
        assert storage.delete_prefix("empty") == 0

    def test_get_total_size(self, storage):
        storage.write_file("org/clip/a.exr", b"x" * 100)
        storage.write_file("org/clip/b.exr", b"y" * 200)
        assert storage.get_total_size("org/clip") == 300

    def test_presigned_url_returns_none(self, storage):
        assert storage.generate_presigned_url("any/key") is None

    def test_presigned_upload_url_returns_none(self, storage):
        assert storage.generate_presigned_upload_url("any/key") is None

    def test_backend_type(self, storage):
        assert storage.backend_type == "local"

    def test_path_traversal_prevention(self, storage):
        with pytest.raises(ValueError, match="traversal"):
            storage.read_file("../../etc/passwd")

    def test_nested_directory_creation(self, storage):
        storage.write_file("deep/nested/dir/file.txt", b"data")
        assert storage.file_exists("deep/nested/dir/file.txt")

    def test_overwrite_existing_file(self, storage):
        storage.write_file("file.txt", b"original")
        storage.write_file("file.txt", b"updated")
        assert storage.read_file("file.txt") == b"updated"


class TestS3StorageImportGuard:
    """Test that S3Storage raises a clear error without boto3."""

    def test_s3_requires_boto3(self, monkeypatch):
        """S3Storage should raise RuntimeError if boto3 is not installed."""
        import sys

        # Hide boto3 temporarily
        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            from web.api.file_storage import S3Storage

            with pytest.raises(RuntimeError, match="boto3"):
                S3Storage(bucket="test-bucket")
        finally:
            if original is not None:
                sys.modules["boto3"] = original
            else:
                sys.modules.pop("boto3", None)


class TestFileStorageSingleton:
    """Test the get_file_storage singleton."""

    def test_default_is_local(self, monkeypatch, tmp_path):
        from web.api.file_storage import get_file_storage, reset_file_storage

        reset_file_storage()
        monkeypatch.setenv("CK_STORAGE_BACKEND", "local")
        monkeypatch.setenv("CK_CLIPS_DIR", str(tmp_path))
        storage = get_file_storage()
        assert storage.backend_type == "local"
        reset_file_storage()

    def test_s3_requires_bucket(self, monkeypatch):
        from web.api.file_storage import get_file_storage, reset_file_storage

        reset_file_storage()
        monkeypatch.setenv("CK_STORAGE_BACKEND", "s3")
        monkeypatch.delenv("CK_S3_BUCKET", raising=False)
        with pytest.raises(RuntimeError, match="CK_S3_BUCKET"):
            get_file_storage()
        reset_file_storage()

    def test_singleton_returns_same_instance(self, monkeypatch, tmp_path):
        from web.api.file_storage import get_file_storage, reset_file_storage

        reset_file_storage()
        monkeypatch.setenv("CK_STORAGE_BACKEND", "local")
        monkeypatch.setenv("CK_CLIPS_DIR", str(tmp_path))
        a = get_file_storage()
        b = get_file_storage()
        assert a is b
        reset_file_storage()


class TestFileStorageInterface:
    """Verify the interface contract."""

    def test_interface_methods(self):
        from web.api.file_storage import FileStorage

        methods = [
            "read_file",
            "write_file",
            "delete_file",
            "list_files",
            "file_exists",
            "get_file_size",
            "delete_prefix",
            "get_total_size",
            "generate_presigned_url",
            "generate_presigned_upload_url",
        ]
        for method in methods:
            assert hasattr(FileStorage, method), f"Missing method: {method}"

    def test_interface_has_backend_type(self):
        from web.api.file_storage import FileStorage

        assert hasattr(FileStorage, "backend_type")


class TestStorageBackendEndpoint:
    """Integration test for the storage-backend system endpoint."""

    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CK_AUTH_ENABLED", "false")
        monkeypatch.setenv("CK_DOCS_PUBLIC", "true")
        monkeypatch.setenv("CK_CLIPS_DIR", str(tmp_path / "clips"))
        monkeypatch.setenv("CK_STORAGE_BACKEND", "local")
        os.makedirs(tmp_path / "clips", exist_ok=True)

        # Reset singleton
        from web.api.file_storage import reset_file_storage

        reset_file_storage()

        import importlib

        import web.api.openapi_config as oc

        importlib.reload(oc)

        import web.api.auth as auth_mod

        importlib.reload(auth_mod)

        from web.api.app import create_app

        app = create_app()
        from fastapi.testclient import TestClient

        c = TestClient(app, raise_server_exceptions=False)
        yield c
        reset_file_storage()

    def test_storage_backend_info(self, client):
        resp = client.get("/api/system/storage-backend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "local"
        assert data["supports_presigned_urls"] is False
