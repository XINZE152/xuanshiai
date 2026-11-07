"""Read-only deployment check; never starts or calls a local backend."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def get_json(base_url: str, path: str, token: str = "") -> object:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(base_url + path, headers=headers), timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://xhztest.xyz")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Use the remote HTTPS origin without credentials or query parameters")
    try:
        spec = get_json(base, "/openapi.json")
        schemas = spec["components"]["schemas"]
        for model in ("WorkspaceLead", "WorkspaceLeadCreate", "WorkspaceLeadUpdate", "WorkspaceLeadFollowUp", "WorkspaceLeadFollowUpCreate"):
            field = schemas[model]["properties"]["intention_level"]
            choices = field.get("anyOf", [field])
            if not any(set(choice.get("enum", [])) == set(range(1, 8)) for choice in choices):
                print(f"FAIL: remote {model}.intention_level does not accept 1-7; deploy/restart the patched backend")
                return 1
        print("PASS: remote OpenAPI exposes all seven lead intention levels")
        token = os.environ.get("XSA_SMOKE_ACCESS_TOKEN", "").strip()
        if not token:
            print("NOT VERIFIED: set XSA_SMOKE_ACCESS_TOKEN to the affected matchmaker access token to check the real list")
            return 2
        page = get_json(base, "/api/v1/matchmaker/workbench/leads?page=1&page_size=100", token)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            print("FAIL: unexpected remote lead page shape")
            return 1
        if any(item.get("intention_level") not in range(1, 8) for item in page["items"]):
            print("FAIL: unexpected intention level in remote response")
            return 1
        print(f"PASS: remote authenticated lead list returned HTTP 200, items={len(page['items'])}")
        if not page["items"]:
            print("NOT VERIFIED: this account has no visible leads; repeat with the affected account")
            return 2
        return 0
    except HTTPError as error:
        print(f"FAIL: remote HTTP {error.code}; check deployment, token validity and matchmaker permissions")
        return 1
    except (URLError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"FAIL: remote check failed ({type(error).__name__}); no credentials or response bodies are printed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
