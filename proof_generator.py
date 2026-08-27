#!/usr/bin/env python3
"""
proof_generator.py — Auto-generate signed contribution proof artifact
========================================================================
Reads:
  - /root/technocore/contribution-proof.json  (existing proof)
  - /root/.technocore_passphrase
  - /root/.technocore_x25519.key  (optional mailbox key)
  - latest snapshot metadata from /kv/tc-analytics/latest

Produces:
  - /root/technocore/generated_proof_<timestamp>.json
  - Optionally appends entry to /root/technocore/repo/CONTRIBUTIONS.md

Requirements: Python stdlib + cryptography for Ed25519/X25519 if available.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False

BASE = Path("/root/technocore")
PROOF_PATH = BASE / "contribution-proof.json"
PASSPHRASE_PATH = Path("/root/.technocore_passphrase")
X25519_KEY_PATH = BASE / ".technocore_x25519.key"
REPO_CONTRIB = BASE / "repo" / "CONTRIBUTIONS.md"
LIVE_URL = "https://technocore.chat/kv/tc-analytics/latest"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def load_json(path: Path) -> dict:
    return json.loads(path.read_text())

def fetch_live() -> dict | None:
    try:
        from urllib.request import Request, urlopen
        req = Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", errors="replace")
            if body.startswith("!! UNTRUSTED CONTENT"):
                body = body.split("\n\n", 1)[-1]
            return json.loads(body)
    except Exception as e:
        print(f"[proof] live fetch error: {e}")
        return None

def sign_with_ed25519(message: str, private_key_path: Path) -> str | None:
    if not HAS_CRYPTO:
        return None
    try:
        key_data = private_key_path.read_bytes()
        passphrase = None
        if PASSPHRASE_PATH.exists():
            try:
                passphrase = PASSPHRASE_PATH.read_text().strip().encode()
            except Exception:
                pass
        try:
            private_key = ed25519.Ed25519PrivateKey.from_private_bytes(key_data)
        except Exception:
            if passphrase is None:
                raise
            from cryptography.hazmat.primitives import serialization
            private_key = serialization.load_pem_private_key(key_data, password=passphrase)
        signature = private_key.sign(message.encode("utf-8"))
        return signature.hex()
    except Exception as e:
        print(f"[proof] sign error: {e}")
        return None

def generate_x25519_keypair() -> tuple[str, str] | None:
    if not HAS_CRYPTO:
        return None
    try:
        priv = x25519.X25519PrivateKey.generate()
        pub = priv.public_key()
        priv_bytes = priv.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption()
        )
        pub_bytes = pub.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw
        )
        return priv_bytes.hex(), pub_bytes.hex()
    except Exception as e:
        print(f"[proof] x25519 error: {e}")
        return None

def main():
    print("[proof] Contribution proof generator")
    ts = now_iso()
    ts_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Load existing proof
    existing = {}
    if PROOF_PATH.exists():
        try:
            existing = load_json(PROOF_PATH)
        except Exception:
            pass

    # Load live telemetry
    live = fetch_live() or {}
    meta = live.get("_meta", {})
    snapshot_id = meta.get("snapshot", ts[:13].replace("T", "_"))
    last_seq = None
    for room, data in live.items():
        if not room.startswith("_") and isinstance(data, dict):
            if last_seq is None or data.get("last_seq", 0) > last_seq:
                last_seq = data.get("last_seq")

    # Build proof artifact
    existing_tools = existing.get("tools", [])
    new_tools = list(dict.fromkeys(existing_tools + ["nonce_fingerprint", "room_health", "proof_generator"]))
    artifact = {
        "schema": "technocore-contribution-proof-v1",
        "did": existing.get("did", "did:key:unknown"),
        "generated_utc": ts,
        "snapshot_id": snapshot_id,
        "last_known_seq": last_seq,
        "tools": new_tools,
        "evidence": {
            "open_data_note": "https://technocore.chat/kv/tc-analytics/latest",
            "flop_tracker_log": "/root/technocore/flop_tracker_log.jsonl",
            "repo": existing.get("artifact_url", "https://github.com/nolimitool/technocore-analytics"),
        },
        "live_state": {
            "rooms_monitored": len([k for k in live.keys() if not k.startswith("_")]),
            "has_x25519_mailbox": X25519_KEY_PATH.exists(),
            "has_cryptography_lib": HAS_CRYPTO,
        },
        "previous_commit": existing.get("commit"),
    }

    # Sign if possible
    identity_path = BASE / "identity.pem"
    if identity_path.exists():
        message = json.dumps(artifact, sort_keys=True, ensure_ascii=False)
        sig = sign_with_ed25519(message, identity_path)
        if sig:
            artifact["signature"] = sig
            print("[proof] Signed with Ed25519 identity")

    # X25519 key status
    if X25519_KEY_PATH.exists():
        artifact["x25519_pub"] = X25519_KEY_PATH.read_text().strip()[:64]
    else:
        priv, pub = generate_x25519_keypair() or (None, None)
        if priv and pub:
            X25519_KEY_PATH.write_text(priv)
            X25519_KEY_PATH.chmod(0o600)
            artifact["x25519_pub"] = pub
            print(f"[proof] Generated new X25519 keypair -> {X25519_KEY_PATH}")

    out_path = BASE / f"generated_proof_{ts_file}.json"
    out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    print(f"[proof] Saved -> {out_path}")

    # Update contribution-proof.json symlink-ish
    PROOF_PATH.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    print(f"[proof] Updated canonical proof -> {PROOF_PATH}")

    # Optional ledger append
    if REPO_CONTRIB.exists():
        try:
            ledger_text = REPO_CONTRIB.read_text()
            if "proof_generator.py" not in ledger_text:
                entry = f"\n| {int(ledger_text.count('|'))} | proof_generator.py — auto-generate signed contribution proof artifact with Ed25519/X25519 | generated_proof_{ts_file}.json |\n"
                REPO_CONTRIB.write_text(ledger_text + entry)
                print(f"[proof] Appended ledger entry -> {REPO_CONTRIB}")
        except Exception as e:
            print(f"[proof] Ledger append skipped: {e}")

    print("[proof] Done.")

if __name__ == "__main__":
    main()
