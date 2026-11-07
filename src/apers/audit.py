"""Hash-chained, tamper-evident audit log.

Each entry's hash covers the previous entry's hash plus the canonical
JSON of the new entry's fields, so reordering, editing, or deleting an
entry breaks the chain. Verification replays the chain from the genesis
entry (prev_hash == "GENESIS") and reports the first broken link.
"""
from __future__ import annotations

import hashlib
import json

from apers.models import AuditEntry

GENESIS = "GENESIS"


def _entry_hash(seq: int, timestamp: str, actor: str, action: str,
                subject_id: str, detail: str, prev_hash: str) -> str:
    payload = {
        "seq": seq,
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "subject_id": subject_id,
        "detail": detail,
        "prev_hash": prev_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only audit log held in memory (persist via to_dict/from_dict)."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def append(self, timestamp: str, actor: str, action: str,
               subject_id: str, detail: str = "") -> AuditEntry:
        prev = self.entries[-1].entry_hash if self.entries else GENESIS
        seq = len(self.entries) + 1
        entry_hash = _entry_hash(seq, timestamp, actor, action, subject_id, detail, prev)
        entry = AuditEntry(seq=seq, timestamp=timestamp, actor=actor,
                           action=action, subject_id=subject_id, detail=detail,
                           prev_hash=prev, entry_hash=entry_hash)
        self.entries.append(entry)
        return entry

    def verify(self) -> tuple[bool, str]:
        """Replay the chain. Returns (ok, message)."""
        prev = GENESIS
        for i, entry in enumerate(self.entries):
            if entry.prev_hash != prev:
                return False, f"entry {entry.seq}: prev_hash mismatch"
            expected = _entry_hash(entry.seq, entry.timestamp, entry.actor,
                                   entry.action, entry.subject_id, entry.detail,
                                   entry.prev_hash)
            if expected != entry.entry_hash:
                return False, f"entry {entry.seq}: hash mismatch (tampered)"
            if entry.seq != i + 1:
                return False, f"entry {entry.seq}: out of order"
            prev = entry.entry_hash
        return True, f"chain intact: {len(self.entries)} entries verified"

    def for_subject(self, subject_id: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.subject_id == subject_id]

    def to_dict(self) -> list[dict]:
        return [e.__dict__ for e in self.entries]
