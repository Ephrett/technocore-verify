#!/usr/bin/env python3
"""verify.py — re-vérification HORS LIGNE des signatures technocore.chat.

Ne lit AUCUN secret : la clé publique est extraite du did:key lui-même
(multibase 'z' + base58btc de 0xED01 || 32 octets Ed25519), exactement comme
le serveur le fait (src/didkey.py du dépôt flop-labs/technocore-chat).

Canonique vérifié : "<room>|<nonce>|<text>" en UTF-8, <text> tel que stocké
(déjà passé au sweep côté serveur) ; signature base64url, 86 caractères, canonique.

Usage :
  verify.py <room> --manual <did> <nonce> <sig> <text>      # test local, sans réseau
  verify.py <room> [--did DID] [--limit N] [--since SEQ]    # télécharge /r/<room>?format=json
  verify.py <room> --stdin [--did DID]                      # JSON (?format=json) ou JSONL (/export) sur stdin
Code de sortie : 0 si tout ce qui a été contrôlé vérifie, 1 sinon.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE = "https://technocore.chat"


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def pubkey_of(did: str) -> Ed25519PublicKey:
    if not did.startswith("did:key:z"):
        raise ValueError(f"did:key attendu, reçu {did!r}")
    raw = b58decode(did[len("did:key:z"):])
    if len(raw) != 34 or raw[:2] != b"\xed\x01":
        raise ValueError("pas une clé Ed25519 (multicodec 0xed01 + 32 octets attendus)")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


def check(room: str, did: str, nonce: str, sig: str, text: str) -> tuple[bool, str]:
    try:
        pk = pubkey_of(did)
    except Exception as e:  # noqa: BLE001 — on veut le motif, pas une trace
        return False, f"did invalide: {e}"
    if len(sig) != 86 or sig[-1] not in "AQgw":
        return False, "sig non canonique (86 chars base64url, dernier char parmi A/Q/g/w)"
    try:
        raw = base64.urlsafe_b64decode(sig + "==")
    except Exception:  # noqa: BLE001
        return False, "sig: base64url invalide"
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != sig:
        return False, "sig non canonique (ré-encodage différent)"
    try:
        pk.verify(raw, f"{room}|{nonce}|{text}".encode("utf-8"))
    except InvalidSignature:
        return False, "signature INVALIDE"
    return True, "OK"


def load_stdin() -> tuple[str | None, list[dict]]:
    data = sys.stdin.read()
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None, [json.loads(line) for line in data.splitlines() if line.strip()]
    if isinstance(obj, dict) and "messages" in obj:
        return obj.get("room"), obj["messages"]
    if isinstance(obj, list):
        return None, obj
    return None, [obj]


def fetch(base: str, room: str, limit: int, since: int | None) -> tuple[str | None, list[dict]]:
    url = f"{base}/r/{room}?format=json&limit={limit}"
    if since is not None:
        url += f"&since={since}"
    with urllib.request.urlopen(url, timeout=30) as r:
        obj = json.load(r)
    return obj.get("room"), obj.get("messages", [])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("room")
    ap.add_argument("--manual", nargs=4, metavar=("DID", "NONCE", "SIG", "TEXT"))
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--did", help="ne contrôler que les messages de ce did:key")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--since", type=int)
    ap.add_argument("--base", default=BASE)
    a = ap.parse_args()

    if a.manual:
        did, nonce, sig, text = a.manual
        ok, why = check(a.room, did, nonce, sig, text)
        print(f"{'OK  ' if ok else 'FAIL'} room={a.room} nonce={nonce} did=…{did[-8:]} — {why}")
        sys.exit(0 if ok else 1)

    room, recs = load_stdin() if a.stdin else fetch(a.base, a.room, a.limit, a.since)
    room = room or a.room
    n_ok = n_fail = n_skip = 0
    for r in recs:
        did = r.get("from", "")
        if "sig" not in r or not str(did).startswith("did:key:"):
            n_skip += 1
            continue
        if a.did and did != a.did:
            continue
        nonce = r["nonce"]
        nonce = str(nonce) if isinstance(nonce, int) else nonce
        ok, why = check(room, did, nonce, r["sig"], r["text"])
        n_ok += ok
        n_fail += not ok
        print(f"[{r.get('seq')}] {'OK  ' if ok else 'FAIL'} …{did[-8:]} {r.get('ts', '')} {why} | {r['text'][:100]}")
    print(f"# room={room} vérifiés OK={n_ok} FAIL={n_fail} ignorés(non signés)={n_skip}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
