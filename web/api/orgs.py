"""Organization model and storage (CRKY-4).

Provides the data layer for multi-tenant organizations:
- ck_orgs: org records (id, name, owner)
- ck_org_members: membership join table (user_id, org_id, role)

When auth is disabled, orgs are not used — all data lives in a single
flat Projects/ directory. When auth is enabled, every user gets a
personal org on approval, and can create or join additional orgs.

Org member roles: owner, admin, member (distinct from platform trust tiers).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Org:
    """Organization record."""

    org_id: str
    name: str
    owner_id: str
    personal: bool = False  # True for auto-created personal orgs
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "name": self.name,
            "owner_id": self.owner_id,
            "personal": self.personal,
            "created_at": self.created_at,
        }


@dataclass
class OrgMember:
    """Membership record linking a user to an org."""

    user_id: str
    org_id: str
    role: str = "member"  # owner, admin, member
    joined_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "org_id": self.org_id,
            "role": self.role,
            "joined_at": self.joined_at,
        }


class OrgStore:
    """In-memory org store backed by the database abstraction.

    Orgs and memberships are stored as JSON in the settings backend
    (ck_settings keys: "orgs", "org_members"). This keeps the storage
    backend interface simple. A future migration can move to dedicated
    Postgres tables for query performance at scale.
    """

    def __init__(self):
        from .database import get_storage

        self._storage = get_storage()
        # Serializes check-then-create for personal orgs. The blob-backed
        # settings store has no row-level uniqueness, so parallel first-login
        # requests (auth middleware + admin approval + tier change) can each
        # observe "no personal org" and each create one. This lock closes the
        # window within a single process.
        self._personal_org_lock = threading.Lock()

    def _load_orgs(self) -> dict[str, dict]:
        return self._storage.get_setting("orgs", {})

    def _save_orgs(self, orgs: dict[str, dict]) -> None:
        self._storage.set_setting("orgs", orgs)

    def _load_members(self) -> list[dict]:
        return self._storage.get_setting("org_members", [])

    def _save_members(self, members: list[dict]) -> None:
        self._storage.set_setting("org_members", members)

    def create_org(self, name: str, owner_id: str, personal: bool = False) -> Org:
        """Create a new org and add the owner as a member."""
        org_id = uuid.uuid4().hex[:12]
        now = time.time()
        org = Org(org_id=org_id, name=name, owner_id=owner_id, personal=personal, created_at=now)

        orgs = self._load_orgs()
        orgs[org_id] = org.to_dict()
        self._save_orgs(orgs)

        # Add owner as member with "owner" role
        self.add_member(org_id, owner_id, role="owner")
        return org

    def get_org(self, org_id: str) -> Org | None:
        orgs = self._load_orgs()
        data = orgs.get(org_id)
        if not data:
            return None
        return Org(**data)

    def rename_org(self, org_id: str, name: str) -> Org | None:
        """Rename an org."""
        orgs = self._load_orgs()
        if org_id not in orgs:
            return None
        orgs[org_id]["name"] = name
        self._save_orgs(orgs)
        return Org(**orgs[org_id])

    def list_orgs(self) -> list[Org]:
        orgs = self._load_orgs()
        return [Org(**v) for v in orgs.values()]

    def list_user_orgs(self, user_id: str) -> list[Org]:
        """List all orgs a user belongs to."""
        members = self._load_members()
        org_ids = {m["org_id"] for m in members if m["user_id"] == user_id}
        orgs = self._load_orgs()
        return [Org(**orgs[oid]) for oid in org_ids if oid in orgs]

    def _list_personal_orgs(self, user_id: str) -> list[Org]:
        """Return every personal org owned by the user, oldest first."""
        personals = [
            org for org in self.list_user_orgs(user_id) if org.personal and org.owner_id == user_id
        ]
        personals.sort(key=lambda o: (o.created_at, o.org_id))
        return personals

    def get_personal_org(self, user_id: str) -> Org | None:
        """Get a user's personal org, if one exists.

        If duplicates already exist from earlier races, return the oldest
        (by created_at) so the caller sees a stable identity.
        """
        personals = self._list_personal_orgs(user_id)
        return personals[0] if personals else None

    def ensure_personal_org(self, user_id: str, email: str, display_name: str = "") -> Org:
        """Get or create the user's personal org.

        Serialized with a process-wide lock so concurrent first-login
        requests do not each create a duplicate. Also defensively merges
        any pre-existing duplicates from prior race incidents by deleting
        the extras and migrating their members into the canonical org.
        """
        with self._personal_org_lock:
            personals = self._list_personal_orgs(user_id)
            if personals:
                canonical = personals[0]
                for extra in personals[1:]:
                    logger.warning(
                        "Removing duplicate personal org %s for user %s (keeping %s)",
                        extra.org_id,
                        user_id,
                        canonical.org_id,
                    )
                    for member in self.list_members(extra.org_id):
                        if member.user_id != user_id:
                            self.add_member(canonical.org_id, member.user_id, role=member.role)
                    self.delete_org(extra.org_id)
                return canonical
            # Prefer the user's display name, fall back to email prefix
            if display_name and display_name.strip():
                name = display_name.strip()
            elif email:
                name = email.split("@")[0]
            else:
                name = user_id[:8]
            return self.create_org(name=f"{name}'s workspace", owner_id=user_id, personal=True)

    def add_member(self, org_id: str, user_id: str, role: str = "member") -> OrgMember:
        """Add a user to an org. No-op if already a member."""
        members = self._load_members()
        # Check if already a member
        for m in members:
            if m["user_id"] == user_id and m["org_id"] == org_id:
                return OrgMember(**m)
        now = time.time()
        member = OrgMember(user_id=user_id, org_id=org_id, role=role, joined_at=now)
        members.append(member.to_dict())
        self._save_members(members)
        return member

    def remove_member(self, org_id: str, user_id: str) -> bool:
        """Remove a user from an org. Returns True if removed."""
        members = self._load_members()
        new_members = [m for m in members if not (m["user_id"] == user_id and m["org_id"] == org_id)]
        if len(new_members) == len(members):
            return False
        self._save_members(new_members)
        return True

    def list_members(self, org_id: str) -> list[OrgMember]:
        """List all members of an org."""
        members = self._load_members()
        return [OrgMember(**m) for m in members if m["org_id"] == org_id]

    def get_member(self, org_id: str, user_id: str) -> OrgMember | None:
        """Get a specific membership."""
        members = self._load_members()
        for m in members:
            if m["user_id"] == user_id and m["org_id"] == org_id:
                return OrgMember(**m)
        return None

    def is_org_admin(self, org_id: str, user_id: str) -> bool:
        """Check if user is an owner or admin of the org."""
        member = self.get_member(org_id, user_id)
        return member is not None and member.role in ("owner", "admin")

    def is_member(self, org_id: str, user_id: str) -> bool:
        """Check if user belongs to the org."""
        return self.get_member(org_id, user_id) is not None

    def update_member_role(self, org_id: str, user_id: str, role: str) -> OrgMember | None:
        """Change a member's role. Returns updated member or None if not found."""
        members = self._load_members()
        for m in members:
            if m["user_id"] == user_id and m["org_id"] == org_id:
                m["role"] = role
                self._save_members(members)
                return OrgMember(**m)
        return None

    def delete_org(self, org_id: str) -> bool:
        """Delete an org and all its memberships."""
        orgs = self._load_orgs()
        if org_id not in orgs:
            return False
        del orgs[org_id]
        self._save_orgs(orgs)
        # Remove all memberships
        members = self._load_members()
        self._save_members([m for m in members if m["org_id"] != org_id])
        return True


# Singleton
_org_store: OrgStore | None = None


def get_org_store() -> OrgStore:
    """Get the org store singleton."""
    global _org_store
    if _org_store is None:
        _org_store = OrgStore()
    return _org_store
