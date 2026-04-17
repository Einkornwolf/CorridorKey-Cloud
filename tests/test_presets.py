"""Tests for pipeline presets (storage + API routes)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from web.api.presets import (
    create_preset,
    delete_preset,
    get_preset,
    list_presets,
    update_preset,
)
from web.api.schemas import (
    InferenceParamsSchema,
    OutputConfigSchema,
    PresetCreateRequest,
    PresetSchema,
    PresetUpdateRequest,
)

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestPresetSchemas:
    def test_create_request_defaults(self):
        req = PresetCreateRequest(name="Test")
        assert req.name == "Test"
        assert req.description == ""
        assert req.params == InferenceParamsSchema()
        assert req.output_config == OutputConfigSchema()
        assert req.is_default is False

    def test_create_request_custom_params(self):
        req = PresetCreateRequest(
            name="Heavy Spill",
            description="Max despill",
            params=InferenceParamsSchema(despill_strength=1.0, auto_despeckle=True, despeckle_size=600),
            output_config=OutputConfigSchema(comp_format="exr"),
            is_default=True,
        )
        assert req.params.despill_strength == 1.0
        assert req.output_config.comp_format == "exr"
        assert req.is_default is True

    def test_create_request_name_validation(self):
        with pytest.raises(ValidationError):
            PresetCreateRequest(name="")  # min_length=1

    def test_update_request_all_none(self):
        req = PresetUpdateRequest()
        assert req.name is None
        assert req.params is None

    def test_preset_schema_roundtrip(self):
        p = PresetSchema(
            id="test-1",
            name="Test",
            scope="org",
            org_id="org-1",
            params=InferenceParamsSchema(despill_strength=0.5),
            output_config=OutputConfigSchema(fg_format="png"),
            is_default=False,
            created_by="user-1",
            created_at=1000.0,
        )
        data = p.model_dump()
        restored = PresetSchema(**data)
        assert restored.id == "test-1"
        assert restored.params.despill_strength == 0.5
        assert restored.output_config.fg_format == "png"


# ---------------------------------------------------------------------------
# Storage layer — uses monkeypatched in-memory backend
# ---------------------------------------------------------------------------


class _InMemoryStorage:
    """Minimal storage mock for testing presets without DB."""

    def __init__(self):
        self._data: dict = {}

    def get_setting(self, key: str, default=None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self._data[key] = value


@pytest.fixture()
def mem_storage(monkeypatch):
    storage = _InMemoryStorage()
    monkeypatch.setattr("web.api.presets.get_storage", lambda: storage)
    return storage


class TestPresetStorage:
    def test_list_presets_no_org(self, mem_storage):
        result = list_presets(None)
        assert len(result) == 0

    def test_list_presets_with_org(self, mem_storage):
        create_preset("org-1", "My Preset", "", InferenceParamsSchema(), OutputConfigSchema(), False, "user-1")
        result = list_presets("org-1")
        assert len(result) == 1

    def test_get_org_preset(self, mem_storage):
        created = create_preset("org-1", "Custom", "desc", InferenceParamsSchema(), OutputConfigSchema(), False, "u1")
        fetched = get_preset(created.id, "org-1")
        assert fetched is not None
        assert fetched.name == "Custom"

    def test_get_missing(self, mem_storage):
        assert get_preset("nonexistent", "org-1") is None

    def test_create_preset(self, mem_storage):
        p = create_preset(
            "org-1",
            "Test",
            "A test preset",
            InferenceParamsSchema(despill_strength=0.3),
            OutputConfigSchema(matte_format="png"),
            False,
            "user-1",
        )
        assert p.id.startswith("org-")
        assert p.scope == "org"
        assert p.org_id == "org-1"
        assert p.params.despill_strength == 0.3
        assert p.output_config.matte_format == "png"
        assert p.created_by == "user-1"
        assert p.created_at > 0

    def test_create_default_clears_others(self, mem_storage):
        p1 = create_preset("org-1", "First", "", InferenceParamsSchema(), OutputConfigSchema(), True, None)
        p2 = create_preset("org-1", "Second", "", InferenceParamsSchema(), OutputConfigSchema(), True, None)
        # p1 should no longer be default
        fetched1 = get_preset(p1.id, "org-1")
        fetched2 = get_preset(p2.id, "org-1")
        assert fetched1 is not None and not fetched1.is_default
        assert fetched2 is not None and fetched2.is_default

    def test_update_preset(self, mem_storage):
        p = create_preset("org-1", "Old", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        updated = update_preset(p.id, "org-1", name="New", params=InferenceParamsSchema(despill_strength=0.7))
        assert updated is not None
        assert updated.name == "New"
        assert updated.params.despill_strength == 0.7

    def test_update_missing_returns_none(self, mem_storage):
        assert update_preset("nope", "org-1", name="x") is None

    def test_update_default_clears_others(self, mem_storage):
        p1 = create_preset("org-1", "A", "", InferenceParamsSchema(), OutputConfigSchema(), True, None)
        p2 = create_preset("org-1", "B", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        update_preset(p2.id, "org-1", is_default=True)
        assert not get_preset(p1.id, "org-1").is_default
        assert get_preset(p2.id, "org-1").is_default

    def test_delete_preset(self, mem_storage):
        p = create_preset("org-1", "Del", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        assert delete_preset(p.id, "org-1") is True
        assert get_preset(p.id, "org-1") is None

    def test_delete_missing_returns_false(self, mem_storage):
        assert delete_preset("nope", "org-1") is False

    def test_org_isolation(self, mem_storage):
        """Presets from org-1 are not visible to org-2."""
        create_preset("org-1", "OrgOne", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        create_preset("org-2", "OrgTwo", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        org1_presets = [p for p in list_presets("org-1") if p.scope == "org"]
        org2_presets = [p for p in list_presets("org-2") if p.scope == "org"]
        assert len(org1_presets) == 1
        assert org1_presets[0].name == "OrgOne"
        assert len(org2_presets) == 1
        assert org2_presets[0].name == "OrgTwo"

    def test_max_presets_per_org_limit(self, mem_storage):
        """Cannot exceed _MAX_PRESETS_PER_ORG presets."""
        from web.api.presets import _MAX_PRESETS_PER_ORG

        for i in range(_MAX_PRESETS_PER_ORG):
            create_preset("org-1", f"Preset {i}", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        with pytest.raises(ValueError, match="Maximum"):
            create_preset("org-1", "One too many", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)

    def test_max_limit_per_org_not_global(self, mem_storage):
        """Limit is per-org, not global."""
        from web.api.presets import _MAX_PRESETS_PER_ORG

        for i in range(_MAX_PRESETS_PER_ORG):
            create_preset("org-1", f"P{i}", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        # org-2 should still be able to create
        p = create_preset("org-2", "First", "", InferenceParamsSchema(), OutputConfigSchema(), False, None)
        assert p.org_id == "org-2"


# ---------------------------------------------------------------------------
# Schema security validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            PresetCreateRequest(name="x" * 101)

    def test_description_max_length(self):
        with pytest.raises(ValidationError):
            PresetCreateRequest(name="Valid", description="x" * 501)

    def test_preset_scope_literal(self):
        p = PresetSchema(id="x", name="x", scope="org")
        assert p.scope == "org"

    def test_preset_scope_rejects_invalid(self):
        with pytest.raises(ValidationError):
            PresetSchema(id="x", name="x", scope="admin")
