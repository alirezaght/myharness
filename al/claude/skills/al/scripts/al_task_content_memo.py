"""One classification's verified content digests, fenced by fresh full-byte reads."""
import os
import al_artifacts as A
from al_schema import refuse


class ContentMemo:
    def __init__(self, *, max_entries=32768, max_reference_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_reference_bytes = max_reference_bytes
        self.entries = {}
        self.reference_bytes = 0

    def digest(self, reference):
        # Unusual references retain A.read's ordinary contract, without memo enrollment.
        frozen = tuple(reference.get(k) for k in ('stored_path', 'sha256', 'kind')) if isinstance(reference, dict) else ()
        eligible = len(frozen) == 3 and all(type(value) is str and len(value) <= limit
                                         for value, limit in zip(frozen, (4096, 64, 256)))
        enabled = os.environ.get('AL_ANALYSIS_CACHE', '1') != '0'
        key = frozen[:2] if eligible else None
        if enabled and key in self.entries:
            return self.entries[key][1]
        digest = A.digest(A.read(reference))
        if enabled and eligible and key not in self.entries:
            size = sum(len(os.fsencode(value)) for value in frozen)
            if (len(self.entries) < self.max_entries
                    and self.reference_bytes + size <= self.max_reference_bytes):
                self.entries[key] = (frozen, digest)
                self.reference_bytes += size
        return digest

    def validate(self):
        # Never use digest() here, including when cache settings change during classification.
        for frozen, expected in self.entries.values():
            reference = dict(zip(('stored_path', 'sha256', 'kind'), frozen))
            try:actual = A.digest(A.read(reference))
            except (OSError, ValueError, KeyError, TypeError):
                refuse('unreadable retained attribution content')
            if actual != expected:
                refuse('retained attribution content differs')
