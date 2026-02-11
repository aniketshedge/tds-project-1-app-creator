from __future__ import annotations

from urllib.parse import urlencode

import requests


def build_auth_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout: int,
) -> str:
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError(data.get("error_description") or "GitHub OAuth token exchange failed")
    return data["access_token"]


def fetch_user_profile(access_token: str, timeout: int) -> dict[str, str]:
    response = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    login = data.get("login")
    if not login:
        raise RuntimeError("GitHub user profile did not contain login")
    return {"username": login}
