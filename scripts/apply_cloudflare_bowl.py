#!/usr/bin/env python3
"""Apply recommended Cloudflare DNS + SSL settings for bowlhockey.com.

Requires ``scripts/cloudflare.env`` (see ``cloudflare.env.example``).
You must add the site to Cloudflare first and point Namecheap nameservers
to Cloudflare (the script prints nameservers if it creates the zone).

  python scripts/apply_cloudflare_bowl.py --dry-run
  python scripts/apply_cloudflare_bowl.py
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / "scripts" / "cloudflare.env"
API = "https://api.cloudflare.com/client/v4"


def _load_env() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise SystemExit(f"Create {ENV_PATH} from scripts/cloudflare.env.example")
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    if not out.get("CLOUDFLARE_API_TOKEN"):
        raise SystemExit("Set CLOUDFLARE_API_TOKEN in scripts/cloudflare.env")
    return out


def _request(
    token: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    dry_run: bool = False,
) -> dict:
    url = f"{API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if dry_run:
        print(f"[dry-run] {method} {path} {body or ''}")
        return {"success": True, "result": []}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cloudflare API {method} {path} failed: {exc.code} {err}") from exc
    if not payload.get("success"):
        raise SystemExit(f"Cloudflare API error: {payload}")
    return payload


def _zone_id(token: str, zone_name: str, *, dry_run: bool) -> tuple[str, list[str]]:
    payload = _request(
        token,
        "GET",
        f"/zones?name={zone_name}",
        dry_run=dry_run,
    )
    results = payload.get("result") or []
    if dry_run:
        return "dry-run-zone-id", ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]
    if not results:
        raise SystemExit(
            f"Zone {zone_name!r} not found in Cloudflare. Add the site in the dashboard "
            "and update Namecheap nameservers to Cloudflare's, then re-run."
        )
    zone = results[0]
    ns = zone.get("name_servers") or []
    return zone["id"], ns


def _ensure_dns(
    token: str,
    zone_id: str,
    origin: str,
    zone_name: str,
    *,
    dry_run: bool,
) -> None:
    existing = _request(
        token,
        "GET",
        f"/zones/{zone_id}/dns_records?per_page=100",
        dry_run=dry_run,
    ).get("result")
    if dry_run:
        existing = []

    def find(name: str, rtype: str) -> dict | None:
        for r in existing:
            if r.get("name") == name and r.get("type") == rtype:
                return r
        return None

    records = [
        ("A", zone_name, origin, True),
        ("A", f"www.{zone_name}", origin, True),
    ]
    for rtype, name, content, proxied in records:
        rec = find(name, rtype)
        if rec:
            if rec.get("content") == content and rec.get("proxied") is proxied:
                print(f"DNS OK: {rtype} {name}")
                continue
            if dry_run:
                print(f"[dry-run] PATCH {rtype} {name} -> {content} proxied={proxied}")
                continue
            _request(
                token,
                "PATCH",
                f"/zones/{zone_id}/dns_records/{rec['id']}",
                body={"content": content, "proxied": proxied, "ttl": 1},
            )
            print(f"DNS updated: {rtype} {name} -> {content} (proxied)")
            continue
        _request(
            token,
            "POST",
            f"/zones/{zone_id}/dns_records",
            body={
                "type": rtype,
                "name": name,
                "content": content,
                "proxied": proxied,
                "ttl": 1,
            },
            dry_run=dry_run,
        )
        print(f"DNS created: {rtype} {name} -> {content} (proxied)")


def _ensure_cache_rules(token: str, zone_id: str, *, dry_run: bool) -> None:
    """Cache /static/ at the edge; bypass /login and /api/."""
    payload = _request(
        token,
        "GET",
        f"/zones/{zone_id}/rulesets/phases/http_request_cache_settings/entrypoint",
        dry_run=dry_run,
    )
    if dry_run:
        print("[dry-run] would upsert cache rules for /static/, bypass /login /api/")
        return

    entry = payload.get("result") or {}
    ruleset_id = entry.get("id")
    desired_rules = [
        {
            "description": "BOWL bypass auth and API",
            "expression": (
                '(http.request.uri.path contains "/login") or '
                '(http.request.uri.path contains "/api/")'
            ),
            "action": "set_cache_settings",
            "action_parameters": {"cache": False},
        },
        {
            "description": "BOWL static assets",
            "expression": '(http.request.uri.path contains "/static/")',
            "action": "set_cache_settings",
            "action_parameters": {
                "cache": True,
                "edge_ttl": {"mode": "override_origin", "default": 604800},
            },
        },
    ]

    if ruleset_id:
        _request(
            token,
            "PUT",
            f"/zones/{zone_id}/rulesets/{ruleset_id}",
            body={"rules": desired_rules},
        )
        print("Cache rules updated (static + bypass login/api)")
        return

    _request(
        token,
        "POST",
        f"/zones/{zone_id}/rulesets",
        body={
            "name": "BOWL cache rules",
            "kind": "zone",
            "phase": "http_request_cache_settings",
            "rules": desired_rules,
        },
    )
    print("Cache rules created (static + bypass login/api)")


def _patch_ssl(token: str, zone_id: str, *, dry_run: bool) -> None:
    for setting, value in (
        ("ssl", "full"),
        ("always_use_https", "on"),
        ("min_tls_version", "1.2"),
    ):
        _request(
            token,
            "PATCH",
            f"/zones/{zone_id}/settings/{setting}",
            body={"value": value},
            dry_run=dry_run,
        )
        print(f"Setting {setting}={value}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = _load_env()
    token = env["CLOUDFLARE_API_TOKEN"]
    zone_name = env.get("CLOUDFLARE_ZONE_NAME", "bowlhockey.com").strip().lower()
    origin = env.get("BOWL_ORIGIN_IPV4", "159.203.6.136").strip()

    zid, nameservers = _zone_id(token, zone_name, dry_run=args.dry_run)
    _patch_ssl(token, zid, dry_run=args.dry_run)
    _ensure_dns(token, zid, origin, zone_name, dry_run=args.dry_run)
    _ensure_cache_rules(token, zid, dry_run=args.dry_run)

    if nameservers:
        print("\nNamecheap → Custom DNS nameservers:")
        for ns in nameservers:
            print(f"  {ns}")
    print(
        "\nIf DNS still resolves to the droplet directly, paste those nameservers at Namecheap "
        "(see docs/CLOUDFLARE-BOWLHOCKEY.md)."
    )
    print("See docs/CLOUDFLARE-BOWLHOCKEY.md for Namecheap nameserver steps if the site is new.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
