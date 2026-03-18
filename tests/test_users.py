"""Tests for user management and approval workflow (CRKY-2)."""

import pytest

from web.api.users import UserRecord, UserStore


@pytest.fixture
def user_store(tmp_path):
    """Create a UserStore backed by a temp JSON file."""
    from web.api import database as db_mod
    from web.api import persist

    persist.init(str(tmp_path))
    db_mod._backend = None
    store = UserStore()
    yield store
    db_mod._backend = None


class TestUserRecord:
    def test_to_dict(self):
        user = UserRecord(user_id="u1", email="a@b.com", tier="member", name="Alice")
        d = user.to_dict()
        assert d["user_id"] == "u1"
        assert d["email"] == "a@b.com"
        assert d["tier"] == "member"
        assert d["name"] == "Alice"

    def test_defaults(self):
        user = UserRecord(user_id="u1", email="a@b.com")
        assert user.tier == "pending"
        assert user.name == ""
        assert user.approved_by == ""


class TestUserStore:
    def test_record_signup(self, user_store):
        user = user_store.record_signup("u1", "alice@test.com", name="Alice")
        assert user.user_id == "u1"
        assert user.email == "alice@test.com"
        assert user.tier == "pending"
        assert user.signed_up_at > 0

    def test_record_signup_idempotent(self, user_store):
        user1 = user_store.record_signup("u1", "alice@test.com")
        user2 = user_store.record_signup("u1", "alice@test.com")
        assert user1.user_id == user2.user_id
        # Should not overwrite
        assert len(user_store.list_users()) == 1

    def test_get_user(self, user_store):
        user_store.record_signup("u1", "alice@test.com")
        user = user_store.get_user("u1")
        assert user is not None
        assert user.email == "alice@test.com"

    def test_get_nonexistent(self, user_store):
        assert user_store.get_user("ghost") is None

    def test_list_users(self, user_store):
        user_store.record_signup("u1", "a@test.com")
        user_store.record_signup("u2", "b@test.com")
        users = user_store.list_users()
        assert len(users) == 2

    def test_list_users_filtered(self, user_store):
        user_store.record_signup("u1", "a@test.com")
        user_store.record_signup("u2", "b@test.com")
        user_store.set_tier("u1", "member", approved_by="admin")
        pending = user_store.list_users(tier_filter="pending")
        assert len(pending) == 1
        assert pending[0].user_id == "u2"

    def test_set_tier(self, user_store):
        user_store.record_signup("u1", "alice@test.com")
        updated = user_store.set_tier("u1", "member", approved_by="admin-1")
        assert updated is not None
        assert updated.tier == "member"
        assert updated.approved_by == "admin-1"
        assert updated.approved_at > 0

    def test_set_tier_nonexistent(self, user_store):
        assert user_store.set_tier("ghost", "member") is None

    def test_delete_user(self, user_store):
        user_store.record_signup("u1", "alice@test.com")
        assert user_store.delete_user("u1")
        assert user_store.get_user("u1") is None

    def test_delete_nonexistent(self, user_store):
        assert not user_store.delete_user("ghost")


class TestUUIDLinking:
    """Tests for the email→UUID re-keying (CRKY-61)."""

    def test_link_uuid(self, user_store):
        user_store.record_signup("alice@test.com", "alice@test.com")
        result = user_store.link_uuid("alice@test.com", "uuid-123")
        assert result is not None
        assert result.user_id == "uuid-123"
        assert result.email == "alice@test.com"
        # Old key gone, new key works
        assert user_store.get_user("alice@test.com") is None
        assert user_store.get_user("uuid-123") is not None

    def test_link_uuid_already_linked(self, user_store):
        user_store.record_signup("alice@test.com", "alice@test.com")
        user_store.link_uuid("alice@test.com", "uuid-123")
        # Second call is idempotent
        result = user_store.link_uuid("alice@test.com", "uuid-123")
        assert result is not None
        assert result.user_id == "uuid-123"

    def test_link_uuid_not_found(self, user_store):
        assert user_store.link_uuid("ghost@test.com", "uuid-456") is None

    def test_get_user_by_email(self, user_store):
        user_store.record_signup("alice@test.com", "alice@test.com")
        user = user_store.get_user_by_email("alice@test.com")
        assert user is not None
        assert user.email == "alice@test.com"

    def test_get_user_by_email_after_link(self, user_store):
        user_store.record_signup("alice@test.com", "alice@test.com")
        user_store.link_uuid("alice@test.com", "uuid-123")
        user = user_store.get_user_by_email("alice@test.com")
        assert user is not None
        assert user.user_id == "uuid-123"

    def test_get_user_by_email_not_found(self, user_store):
        assert user_store.get_user_by_email("nope@test.com") is None


class TestApprovalWorkflow:
    """Integration-style tests for the approval workflow."""

    def test_signup_then_approve(self, user_store):
        # User signs up
        user_store.record_signup("u1", "alice@test.com")
        assert user_store.get_user("u1").tier == "pending"

        # Admin approves
        updated = user_store.set_tier("u1", "member", approved_by="admin-1")
        assert updated.tier == "member"

        # Pending list is now empty
        assert user_store.list_users(tier_filter="pending") == []

    def test_signup_then_reject(self, user_store):
        user_store.record_signup("u1", "alice@test.com")
        updated = user_store.set_tier("u1", "rejected", approved_by="admin-1")
        assert updated.tier == "rejected"

    def test_promote_to_contributor(self, user_store):
        user_store.record_signup("u1", "alice@test.com")
        user_store.set_tier("u1", "member", approved_by="admin-1")
        user_store.set_tier("u1", "contributor")
        assert user_store.get_user("u1").tier == "contributor"
