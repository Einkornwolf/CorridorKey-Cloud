"""Organization model and storage (CRKY-4, CRKY-172).

Provides the data layer for multi-tenant organizations:
- ck.orgs: org records (id, name, owner, personal flag)
- ck.org_members: membership join table (org_id, user_id, role)

When auth is disabled, orgs are not used — all data lives in a single
flat Projects/ directory. When auth is enabled, every user gets a
personal org on approval, and can create or join additional orgs.

Org member roles: owner, admin, member (distinct from platform trust tiers).

Storage: CRKY-172 moved both tables out of the ck.settings JSON blob
into dedicated Postgres tables. A partial unique index on (owner_id)
WHERE personal = TRUE makes duplicate personal org creation a
database-level conflict, and FK ON DELETE CASCADE from org_members to
orgs makes delete_org a single atomic statement. The JSON storage path
is kept as a fallback for dev/test setups without Postgres; the
process-wide lock still guards the fallback's check-then-create, but
the Postgres path does not need it.
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


def _row_to_org(row: tuple) -> Org:
    return Org(
        org_id=row[0],
        name=row[1],
        owner_id=row[2],
        personal=bool(row[3]),
        created_at=float(row[4] or 0),
    )


def _row_to_member(row: tuple) -> OrgMember:
    return OrgMember(
        org_id=row[0],
        user_id=row[1],
        role=row[2],
        joined_at=float(row[3] or 0),
    )


_ORG_COLS = "org_id, name, owner_id, personal, created_at"
_MEMBER_COLS = "org_id, user_id, role, joined_at"


class OrgStore:
    """Org store backed by ck.orgs / ck.org_members, with JSON fallback."""

    def __init__(self):
        from .database import get_storage

        self._storage = get_storage()
        # Protects the JSON fallback path only. The Postgres path uses a
        # partial unique index to enforce one-personal-org-per-owner, so
        # it does not need application-level locking.
        self._personal_org_lock = threading.Lock()

    # -- JSON fallback helpers -------------------------------------------------

    def _load_orgs_blob(self) -> dict[str, dict]:
        return self._storage.get_setting("orgs", {})

    def _save_orgs_blob(self, orgs: dict[str, dict]) -> None:
        self._storage.set_setting("orgs", orgs)

    def _load_members_blob(self) -> list[dict]:
        return self._storage.get_setting("org_members", [])

    def _save_members_blob(self, members: list[dict]) -> None:
        self._storage.set_setting("org_members", members)

    # -- Public API ------------------------------------------------------------

    def create_org(self, name: str, owner_id: str, personal: bool = False) -> Org:
        """Create a new org and add the owner as a member."""
        from .database import get_pg_conn

        org_id = uuid.uuid4().hex[:12]
        now = time.time()
        org = Org(org_id=org_id, name=name, owner_id=owner_id, personal=personal, created_at=now)

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO ck.orgs ({_ORG_COLS})
                        VALUES (%s, %s, %s, %s, %s)""",
                    (org_id, name, owner_id, personal, now),
                )
                cur.execute(
                    f"""INSERT INTO ck.org_members ({_MEMBER_COLS})
                        VALUES (%s, %s, 'owner', %s)
                        ON CONFLICT DO NOTHING""",
                    (org_id, owner_id, now),
                )
                cur.close()
                return org

        orgs = self._load_orgs_blob()
        orgs[org_id] = org.to_dict()
        self._save_orgs_blob(orgs)
        self.add_member(org_id, owner_id, role="owner")
        return org

    def get_org(self, org_id: str) -> Org | None:
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT {_ORG_COLS} FROM ck.orgs WHERE org_id = %s",
                    (org_id,),
                )
                row = cur.fetchone()
                cur.close()
                return _row_to_org(row) if row else None

        data = self._load_orgs_blob().get(org_id)
        return Org(**data) if data else None

    def rename_org(self, org_id: str, name: str) -> Org | None:
        """Rename an org."""
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""UPDATE ck.orgs SET name = %s WHERE org_id = %s
                        RETURNING {_ORG_COLS}""",
                    (name, org_id),
                )
                row = cur.fetchone()
                cur.close()
                return _row_to_org(row) if row else None

        orgs = self._load_orgs_blob()
        if org_id not in orgs:
            return None
        orgs[org_id]["name"] = name
        self._save_orgs_blob(orgs)
        return Org(**orgs[org_id])

    def list_orgs(self) -> list[Org]:
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(f"SELECT {_ORG_COLS} FROM ck.orgs")
                rows = cur.fetchall()
                cur.close()
                return [_row_to_org(r) for r in rows]

        return [Org(**v) for v in self._load_orgs_blob().values()]

    def list_user_orgs(self, user_id: str) -> list[Org]:
        """List all orgs a user belongs to."""
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""SELECT {_ORG_COLS} FROM ck.orgs
                        WHERE org_id IN (SELECT org_id FROM ck.org_members WHERE user_id = %s)""",
                    (user_id,),
                )
                rows = cur.fetchall()
                cur.close()
                return [_row_to_org(r) for r in rows]

        members = self._load_members_blob()
        org_ids = {m["org_id"] for m in members if m["user_id"] == user_id}
        orgs = self._load_orgs_blob()
        return [Org(**orgs[oid]) for oid in org_ids if oid in orgs]

    def _list_personal_orgs_pg(self, user_id: str) -> list[Org]:
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""SELECT {_ORG_COLS} FROM ck.orgs
                        WHERE owner_id = %s AND personal = TRUE
                        ORDER BY created_at ASC, org_id ASC""",
                    (user_id,),
                )
                rows = cur.fetchall()
                cur.close()
                return [_row_to_org(r) for r in rows]
        return []

    def _list_personal_orgs_blob(self, user_id: str) -> list[Org]:
        personals = [org for org in self.list_user_orgs(user_id) if org.personal and org.owner_id == user_id]
        personals.sort(key=lambda o: (o.created_at, o.org_id))
        return personals

    def get_personal_org(self, user_id: str) -> Org | None:
        """Get a user's personal org, if one exists.

        If duplicates still exist from legacy JSON-era races, return the
        oldest (by created_at) so the caller sees a stable identity. On
        the Postgres path the partial unique index prevents new
        duplicates, so this is only relevant to the JSON fallback and
        to blobs backfilled from the legacy blob.
        """
        pg_personals = self._list_personal_orgs_pg(user_id)
        if pg_personals:
            return pg_personals[0]
        personals = self._list_personal_orgs_blob(user_id)
        return personals[0] if personals else None

    def ensure_personal_org(self, user_id: str, email: str, display_name: str = "") -> Org:
        """Get or create the user's personal org.

        Postgres path: INSERT ... ON CONFLICT DO NOTHING on the partial
        unique index (owner_id) WHERE personal = TRUE. If the insert
        conflicted, SELECT the existing row. Either way the owner
        membership is upserted so crashes between the two statements
        self-heal on the next call.

        JSON fallback path: still uses a process-wide lock plus a
        defensive dedupe that collapses pre-existing duplicates from
        prior races. The lock does not protect multiple processes.
        """
        from .database import get_pg_conn

        if display_name and display_name.strip():
            derived_name = display_name.strip()
        elif email:
            derived_name = email.split("@")[0]
        else:
            derived_name = user_id[:8]
        workspace_name = f"{derived_name}'s workspace"

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                org_id = uuid.uuid4().hex[:12]
                now = time.time()
                cur.execute(
                    f"""INSERT INTO ck.orgs ({_ORG_COLS})
                        VALUES (%s, %s, %s, TRUE, %s)
                        ON CONFLICT (owner_id) WHERE personal = TRUE DO NOTHING
                        RETURNING {_ORG_COLS}""",
                    (org_id, workspace_name, user_id, now),
                )
                row = cur.fetchone()
                if row is None:
                    # A personal org already exists for this owner — fetch it.
                    cur.execute(
                        f"""SELECT {_ORG_COLS} FROM ck.orgs
                            WHERE owner_id = %s AND personal = TRUE
                            ORDER BY created_at ASC, org_id ASC LIMIT 1""",
                        (user_id,),
                    )
                    row = cur.fetchone()
                org = _row_to_org(row)
                # Idempotently ensure the owner is a member of their own
                # personal org, even if an earlier call crashed between the
                # two inserts.
                cur.execute(
                    f"""INSERT INTO ck.org_members ({_MEMBER_COLS})
                        VALUES (%s, %s, 'owner', %s)
                        ON CONFLICT (org_id, user_id) DO NOTHING""",
                    (org.org_id, user_id, org.created_at or time.time()),
                )
                cur.close()
                return org

        with self._personal_org_lock:
            personals = self._list_personal_orgs_blob(user_id)
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
            return self.create_org(name=workspace_name, owner_id=user_id, personal=True)

    def add_member(self, org_id: str, user_id: str, role: str = "member") -> OrgMember:
        """Add a user to an org. No-op if already a member."""
        from .database import get_pg_conn

        now = time.time()
        member = OrgMember(user_id=user_id, org_id=org_id, role=role, joined_at=now)

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""INSERT INTO ck.org_members ({_MEMBER_COLS})
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (org_id, user_id) DO NOTHING
                        RETURNING {_MEMBER_COLS}""",
                    (org_id, user_id, role, now),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"""SELECT {_MEMBER_COLS} FROM ck.org_members
                            WHERE org_id = %s AND user_id = %s""",
                        (org_id, user_id),
                    )
                    row = cur.fetchone()
                cur.close()
                return _row_to_member(row) if row else member

        members = self._load_members_blob()
        for m in members:
            if m["user_id"] == user_id and m["org_id"] == org_id:
                return OrgMember(**m)
        members.append(member.to_dict())
        self._save_members_blob(members)
        return member

    def remove_member(self, org_id: str, user_id: str) -> bool:
        """Remove a user from an org. Returns True if removed."""
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM ck.org_members WHERE org_id = %s AND user_id = %s",
                    (org_id, user_id),
                )
                removed = cur.rowcount > 0
                cur.close()
                return removed

        members = self._load_members_blob()
        new_members = [m for m in members if not (m["user_id"] == user_id and m["org_id"] == org_id)]
        if len(new_members) == len(members):
            return False
        self._save_members_blob(new_members)
        return True

    def list_members(self, org_id: str) -> list[OrgMember]:
        """List all members of an org."""
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT {_MEMBER_COLS} FROM ck.org_members WHERE org_id = %s",
                    (org_id,),
                )
                rows = cur.fetchall()
                cur.close()
                return [_row_to_member(r) for r in rows]

        members = self._load_members_blob()
        return [OrgMember(**m) for m in members if m["org_id"] == org_id]

    def get_member(self, org_id: str, user_id: str) -> OrgMember | None:
        """Get a specific membership."""
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""SELECT {_MEMBER_COLS} FROM ck.org_members
                        WHERE org_id = %s AND user_id = %s""",
                    (org_id, user_id),
                )
                row = cur.fetchone()
                cur.close()
                return _row_to_member(row) if row else None

        members = self._load_members_blob()
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
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute(
                    f"""UPDATE ck.org_members SET role = %s
                        WHERE org_id = %s AND user_id = %s
                        RETURNING {_MEMBER_COLS}""",
                    (role, org_id, user_id),
                )
                row = cur.fetchone()
                cur.close()
                return _row_to_member(row) if row else None

        members = self._load_members_blob()
        for m in members:
            if m["user_id"] == user_id and m["org_id"] == org_id:
                m["role"] = role
                self._save_members_blob(members)
                return OrgMember(**m)
        return None

    def delete_org(self, org_id: str) -> bool:
        """Delete an org and all its memberships.

        Postgres path is a single DELETE; FK ON DELETE CASCADE handles
        the members. JSON fallback still writes two blobs, which is
        non-transactional — the same bug CRKY-172 is about — but is
        only reachable in dev/test setups without Postgres.
        """
        from .database import get_pg_conn

        with get_pg_conn() as conn:
            if conn is not None:
                cur = conn.cursor()
                cur.execute("DELETE FROM ck.orgs WHERE org_id = %s", (org_id,))
                removed = cur.rowcount > 0
                cur.close()
                return removed

        orgs = self._load_orgs_blob()
        if org_id not in orgs:
            return False
        del orgs[org_id]
        self._save_orgs_blob(orgs)
        members = self._load_members_blob()
        self._save_members_blob([m for m in members if m["org_id"] != org_id])
        return True


def heal_missing_personal_orgs() -> int:
    """Full startup heal for org/membership/token consistency.

    Covers five failure modes discovered during the 2026-04-15
    incident where a manual org prune + the CRKY-61 email-to-UUID
    migration left users, orgs, memberships, and node tokens in
    various broken states:

    1. Ghost memberships whose user_id is an email (pre-CRKY-61)
       or a UUID that no longer exists in ck.users. These prevent
       the heal from inserting the correct UUID-keyed membership
       because the ghost row occupies the (org_id, user_id) slot
       for the email key. Delete them first.
    2. Orphan personal orgs whose owner_id doesn't resolve to a
       ck.users row. The owner is gone; the org serves nobody.
    3. Missing personal orgs for approved users who lost theirs to
       a manual prune or a dedup pass.
    4. Missing owner membership rows for personal orgs that exist
       but whose owner isn't in ck.org_members for that org (the
       "Hunt" bug: org exists, membership doesn't, UI says "No
       organizations").
    5. Node tokens whose org_id points at a deleted org. Re-links
       them to the token creator's current personal org so nodes
       re-register under the right org on next heartbeat.

    Idempotent. Every statement uses EXISTS guards, ON CONFLICT DO
    NOTHING, or conditional JOINs so re-running is a no-op when the
    state is already consistent. Safe to call on every container
    startup.

    Returns the number of personal orgs inserted (step 3).
    """
    from .database import get_pg_conn

    with get_pg_conn() as conn:
        if conn is None:
            return 0
        cur = conn.cursor()
        try:
            # Step 1: delete ghost memberships whose user_id isn't in
            # ck.users (email-keyed leftovers from pre-CRKY-61).
            cur.execute(
                """
                DELETE FROM ck.org_members m
                WHERE NOT EXISTS (
                    SELECT 1 FROM ck.users u WHERE u.user_id = m.user_id
                )
                """
            )
            ghost_members = cur.rowcount
            if ghost_members:
                logger.info("Org heal: removed %d ghost membership(s)", ghost_members)

            # Step 2: drop orphan personal orgs whose owner is gone.
            cur.execute(
                """
                DELETE FROM ck.orgs o
                WHERE o.personal = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM ck.users u WHERE u.user_id = o.owner_id
                  )
                """
            )
            orphan_orgs = cur.rowcount
            if orphan_orgs:
                logger.info("Org heal: removed %d orphan personal org(s)", orphan_orgs)

            # Step 3: insert missing personal orgs.
            cur.execute(
                """
                INSERT INTO ck.orgs (org_id, name, owner_id, personal, created_at)
                SELECT
                    substring(replace(gen_random_uuid()::text, '-', ''), 1, 12),
                    COALESCE(NULLIF(u.name, ''), split_part(u.email, '@', 1))
                        || '''s workspace',
                    u.user_id,
                    TRUE,
                    EXTRACT(EPOCH FROM NOW())
                FROM ck.users u
                WHERE u.tier NOT IN ('pending', 'rejected')
                  AND NOT EXISTS (
                      SELECT 1 FROM ck.orgs o
                      WHERE o.owner_id = u.user_id AND o.personal = TRUE
                  )
                ON CONFLICT (owner_id) WHERE personal = TRUE DO NOTHING
                """
            )
            inserted = cur.rowcount

            # Step 4: ensure owner membership for every personal org.
            cur.execute(
                """
                INSERT INTO ck.org_members (org_id, user_id, role, joined_at)
                SELECT o.org_id, o.owner_id, 'owner', o.created_at
                FROM ck.orgs o
                WHERE o.personal = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM ck.org_members m
                      WHERE m.org_id = o.org_id AND m.user_id = o.owner_id
                  )
                ON CONFLICT (org_id, user_id) DO NOTHING
                """
            )
            membership_fixes = cur.rowcount

            # Step 5: re-link node tokens whose org_id is stale.
            cur.execute(
                """
                UPDATE ck.node_tokens t
                SET org_id = new_org.org_id
                FROM (
                    SELECT t2.token, o.org_id
                    FROM ck.node_tokens t2
                    JOIN ck.orgs o
                      ON o.owner_id = t2.created_by AND o.personal = TRUE
                    WHERE NOT EXISTS (
                        SELECT 1 FROM ck.orgs o2 WHERE o2.org_id = t2.org_id
                    )
                ) new_org
                WHERE t.token = new_org.token
                """
            )
            token_fixes = cur.rowcount

            if inserted or membership_fixes or ghost_members or orphan_orgs or token_fixes:
                logger.info(
                    "Org heal complete: "
                    "+%d org(s), +%d membership(s), "
                    "-%d ghost member(s), -%d orphan org(s), "
                    "~%d token(s) re-linked",
                    inserted,
                    membership_fixes,
                    ghost_members,
                    orphan_orgs,
                    token_fixes,
                )
            return int(inserted or 0)
        finally:
            cur.close()


# Singleton
_org_store: OrgStore | None = None


def get_org_store() -> OrgStore:
    """Get the org store singleton."""
    global _org_store
    if _org_store is None:
        _org_store = OrgStore()
    return _org_store
