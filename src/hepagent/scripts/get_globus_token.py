#!/usr/bin/env python3
import argparse
import json
import os
import stat
import time
from pathlib import Path

import globus_sdk
from globus_sdk.exc import GlobusAPIError

CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"  # dingpf iri-nersc
RESOURCE_SERVER = "auth.globus.org"
REQUIRED_SCOPES = {
    "openid",
    "profile",
    "email",
    "urn:globus:auth:scope:auth.globus.org:view_identities",
}


def parse_args() -> argparse.Namespace:
    default_token_file = Path.home() / ".globus" / "auth_tokens.json"
    parser = argparse.ArgumentParser(
        description=(
            "Get Globus Auth tokens with required scopes. "
            "Tokens are saved to a secure local file by default."
        )
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=default_token_file,
        help=f"Path for saved token JSON (default: {default_token_file})",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Print the access token to stdout (off by default).",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Skip refresh and force interactive browser login.",
    )
    return parser.parse_args()


def parse_scope_string(scope_string: str) -> set[str]:
    return set(scope_string.split()) if scope_string else set()


def ensure_private_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)


def load_tokens(token_file: Path) -> dict | None:
    if not token_file.exists():
        return None
    with token_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_file: Path, tokens: dict) -> None:
    ensure_private_parent_dir(token_file)
    tmp = token_file.with_suffix(".tmp")
    with os.fdopen(
        os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(tokens, f, indent=2)
    os.replace(tmp, token_file)
    os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)


def interactive_login(client: globus_sdk.NativeAppAuthClient) -> dict:
    client.oauth2_start_flow(
        requested_scopes=" ".join(sorted(REQUIRED_SCOPES)),
        refresh_tokens=True,
    )
    print("Open this URL, login, and consent:")
    print(client.oauth2_get_authorize_url())
    code = input("\nEnter authorization code: ").strip()
    token_response = client.oauth2_exchange_code_for_tokens(code)
    return token_response.by_resource_server[RESOURCE_SERVER]


def refresh_tokens(client: globus_sdk.NativeAppAuthClient, refresh_token: str) -> dict | None:
    try:
        token_response = client.oauth2_refresh_token(refresh_token)
        return token_response.by_resource_server[RESOURCE_SERVER]
    except GlobusAPIError as exc:
        print(f"Refresh failed ({exc.http_status}); switching to interactive login.")
        return None


def main() -> None:
    args = parse_args()
    client = globus_sdk.NativeAppAuthClient(CLIENT_ID)

    auth_data = None
    if not args.force_login:
        stored = load_tokens(args.token_file)
        if stored and stored.get("refresh_token"):
            auth_data = refresh_tokens(client, stored["refresh_token"])

    if auth_data is None:
        auth_data = interactive_login(client)

    granted = parse_scope_string(auth_data.get("scope", ""))
    missing = REQUIRED_SCOPES - granted
    if missing:
        raise RuntimeError(f"Missing required scopes: {sorted(missing)}")

    save_tokens(args.token_file, auth_data)

    expires_at = auth_data.get("expires_at_seconds")
    if expires_at:
        ttl = int(expires_at - time.time())
        print(f"\nAccess token valid for ~{max(ttl, 0)} seconds.")

    print(f"Saved token data to {args.token_file}")
    print(f"Granted scopes: {auth_data.get('scope', '')}")

    if args.print_token:
        print("\nAccess token:")
        print(auth_data["access_token"])
    else:
        print("Access token not printed (use --print-token to display it).")


if __name__ == "__main__":
    main()
