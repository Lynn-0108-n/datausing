"""审计溯源：哈希链 + HMAC 签名。"""

from .chain import (
    EXPORT_MAX_LIMIT,
    GENESIS,
    append_entry,
    append_entry_conn,
    canonical_hash,
    entry_hash_of,
    export_entries,
    latest_entries,
    list_entries,
    sign,
    verify_chain,
    verify_signature,
)

__all__ = [
    "EXPORT_MAX_LIMIT",
    "GENESIS",
    "append_entry",
    "append_entry_conn",
    "canonical_hash",
    "entry_hash_of",
    "export_entries",
    "latest_entries",
    "list_entries",
    "sign",
    "verify_chain",
    "verify_signature",
]
