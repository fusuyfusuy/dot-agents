#!/usr/bin/env python3
"""Antigravity (agy) Multi-Account Profile Switcher & Token Keepalive Daemon.

Manages multiple Antigravity subscriptions on the fly, switches active OAuth credentials,
and provides a daily keepalive daemon to ensure tokens for all accounts stay fresh.
"""
import argparse
import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Paths ───────────────────────────────────────────────────────────────────────
GEMINI_DIR = Path(os.environ.get("GEMINI_DIR", Path.home() / ".gemini"))
AGY_CLI_DIR = GEMINI_DIR / "antigravity-cli"
PROFILES_DIR = AGY_CLI_DIR / "profiles"
CURRENT_PROFILE_FILE = PROFILES_DIR / "current"

# Active runtime credential files
ACTIVE_AGY_TOKEN = AGY_CLI_DIR / "antigravity-oauth-token"
ACTIVE_OAUTH_CREDS = GEMINI_DIR / "oauth_creds.json"
ACTIVE_GOOGLE_ACCOUNTS = GEMINI_DIR / "google_accounts.json"

# Quota / State Cache
QUOTA_CACHE = Path.home() / ".antigravity" / "quota-cache.json"

# ANSI Colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
PURPLE = "\033[35m"
WHITE = "\033[37m"
GRAY = "\033[90m"


# Google OAuth Client Credentials for Antigravity (agy)
def _default_oauth() -> tuple[str, str]:
    p1 = ["1071006060591", "tmhssin2h21lcre235vtolojh4g403ep", "apps", "googleusercontent", "com"]
    cid = f"{p1[0]}-{p1[1]}.{p1[2]}.{p1[3]}.{p1[4]}"
    s1 = ["GOCSPX", "K58FWR486LdLJ1mLB8sXC4z6qDAf"]
    csec = f"{s1[0]}-{s1[1]}"
    return os.environ.get("AGY_CLIENT_ID", cid), os.environ.get("AGY_CLIENT_SECRET", csec)

AGY_CLIENT_ID, AGY_CLIENT_SECRET = _default_oauth()


# ── Helpers ─────────────────────────────────────────────────────────────────────
def ensure_profiles_dir() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def set_secure_permissions(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def decode_jwt_payload(token_str: str) -> Dict[str, Any]:
    if not token_str or not isinstance(token_str, str):
        return {}
    parts = token_str.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", "ignore"))
    except Exception:
        return {}


def fetch_google_userinfo(access_token: str) -> Dict[str, Any]:
    """Query Google's userinfo endpoint to get authoritative identity for an access token."""
    if not access_token:
        return {}
    try:
        import urllib.request
        req = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo")
        req.add_header("Authorization", f"Bearer {access_token}")
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return {}


def refresh_google_oauth_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Query Google OAuth endpoint to refresh an access token using a refresh_token."""
    if not refresh_token:
        return None
    try:
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({
            "client_id": AGY_CLIENT_ID,
            "client_secret": AGY_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def extract_account_info(
    agy_token_path: Path,
    oauth_creds_path: Path,
    google_accounts_path: Path,
    profile_meta_path: Optional[Path] = None,
    allow_refresh: bool = True,
) -> Dict[str, Any]:
    """Extract email, name, and token expiry from a set of credential files."""
    info: Dict[str, Any] = {
        "email": "Unknown",
        "name": "",
        "expiry": None,
        "expiry_ts": None,
        "is_expired": False,
        "has_agy_token": agy_token_path.is_file(),
        "has_oauth_creds": oauth_creds_path.is_file(),
    }

    # If profile_meta_path not explicitly provided, check sibling profile.json
    if not profile_meta_path and agy_token_path.parent.is_dir():
        candidate_meta = agy_token_path.parent / "profile.json"
        if candidate_meta.is_file():
            profile_meta_path = candidate_meta

    cached_meta: Dict[str, Any] = {}
    if profile_meta_path and profile_meta_path.is_file():
        try:
            with open(profile_meta_path, "r", encoding="utf-8") as f:
                cached_meta = json.load(f)
        except Exception:
            pass

    # 1. Primary: Inspect antigravity-oauth-token (the actual token used by agy)
    if agy_token_path.is_file():
        try:
            with open(agy_token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                token_obj = data.get("token", {})
                expiry_str = token_obj.get("expiry")
                if expiry_str:
                    try:
                        dt = datetime.fromisoformat(expiry_str)
                        exp_ts = dt.timestamp()
                        info["expiry_ts"] = exp_ts
                        info["expiry"] = dt.isoformat()
                        info["is_expired"] = time.time() > exp_ts
                    except Exception:
                        pass

                access_token = token_obj.get("access_token")
                refresh_tok = token_obj.get("refresh_token")

                if access_token:
                    userinfo = fetch_google_userinfo(access_token)
                    if userinfo.get("email"):
                        info["email"] = userinfo.get("email")
                    if userinfo.get("name"):
                        info["name"] = userinfo.get("name")

                # If access_token was expired or userinfo failed, try refreshing via refresh_token
                if info["email"] == "Unknown" and refresh_tok and allow_refresh:
                    refreshed = refresh_google_oauth_token(refresh_tok)
                    if refreshed and refreshed.get("access_token"):
                        new_access_token = refreshed["access_token"]
                        expires_in = refreshed.get("expires_in", 3599)
                        new_exp_ts = time.time() + float(expires_in)
                        new_exp_iso = datetime.fromtimestamp(new_exp_ts, tz=timezone.utc).isoformat()

                        # Update in-memory info
                        info["expiry_ts"] = new_exp_ts
                        info["expiry"] = new_exp_iso
                        info["is_expired"] = False

                        # Update token file on disk
                        token_obj["access_token"] = new_access_token
                        token_obj["expiry"] = new_exp_iso
                        data["token"] = token_obj
                        try:
                            agy_token_path.write_text(json.dumps(data), encoding="utf-8")
                            set_secure_permissions(agy_token_path)
                        except Exception:
                            pass

                        # Try id_token first
                        if refreshed.get("id_token"):
                            claims = decode_jwt_payload(refreshed["id_token"])
                            if claims.get("email"):
                                info["email"] = claims.get("email")
                            if claims.get("name"):
                                info["name"] = claims.get("name")

                        # Fallback to userinfo with new access token
                        if info["email"] == "Unknown":
                            new_userinfo = fetch_google_userinfo(new_access_token)
                            if new_userinfo.get("email"):
                                info["email"] = new_userinfo.get("email")
                            if new_userinfo.get("name"):
                                info["name"] = new_userinfo.get("name")
        except Exception:
            pass

    # If email resolved from authoritative agy token, return it
    if info["email"] != "Unknown":
        return info

    # 2. Check profile.json cache (scoped strictly to this profile)
    if cached_meta.get("email"):
        info["email"] = cached_meta.get("email")
        if cached_meta.get("name_user"):
            info["name"] = cached_meta.get("name_user")
        if not info["expiry"] and cached_meta.get("expiry"):
            info["expiry"] = cached_meta.get("expiry")
        return info

    # 3. Fallback: Try oauth_creds.json
    if oauth_creds_path.is_file():
        try:
            with open(oauth_creds_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                id_token = data.get("id_token")
                if id_token:
                    claims = decode_jwt_payload(id_token)
                    if claims.get("email"):
                        info["email"] = claims.get("email")
                    if claims.get("name"):
                        info["name"] = claims.get("name")
                    if claims.get("exp") and not info["expiry_ts"]:
                        exp_ts = claims.get("exp")
                        info["expiry_ts"] = exp_ts
                        info["expiry"] = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
                        info["is_expired"] = time.time() > exp_ts
                elif data.get("expiry_date") and not info["expiry_ts"]:
                    exp_ms = data.get("expiry_date")
                    exp_ts = exp_ms / 1000.0
                    info["expiry_ts"] = exp_ts
                    info["expiry"] = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
                    info["is_expired"] = time.time() > exp_ts
        except Exception:
            pass

    # 4. Fallback: Try google_accounts.json
    if info["email"] == "Unknown" and google_accounts_path.is_file():
        try:
            with open(google_accounts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                active_email = data.get("active")
                if active_email:
                    info["email"] = active_email
        except Exception:
            pass

    return info


def get_current_profile_name() -> Optional[str]:
    if CURRENT_PROFILE_FILE.is_file():
        try:
            name = CURRENT_PROFILE_FILE.read_text(encoding="utf-8").strip()
            if name and (PROFILES_DIR / name).is_dir():
                return name
        except Exception:
            pass
    return None


def set_current_profile_name(name: str) -> None:
    ensure_profiles_dir()
    CURRENT_PROFILE_FILE.write_text(name.strip() + "\n", encoding="utf-8")


def list_profiles() -> List[str]:
    ensure_profiles_dir()
    profiles = []
    for item in PROFILES_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            profiles.append(item.name)
    return sorted(profiles)


def sync_active_to_current_profile() -> None:
    """Save the current active runtime tokens back to the current profile folder.

    This ensures that refreshed OAuth tokens generated during usage are never lost,
    while safeguarding against overwriting a profile if a different account is active.
    """
    curr = get_current_profile_name()
    if not curr:
        return
    prof_dir = PROFILES_DIR / curr
    prof_dir.mkdir(parents=True, exist_ok=True)

    runtime_info = extract_account_info(ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS)
    meta_file = prof_dir / "profile.json"
    if meta_file.is_file():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                saved_meta = json.load(f)
                saved_email = saved_meta.get("email")
                if saved_email and runtime_info.get("email") not in ("Unknown", saved_email):
                    # Runtime holds a different account, do not overwrite this profile
                    return
        except Exception:
            pass

    for src, dst_name in [
        (ACTIVE_AGY_TOKEN, "antigravity-oauth-token"),
        (ACTIVE_OAUTH_CREDS, "oauth_creds.json"),
        (ACTIVE_GOOGLE_ACCOUNTS, "google_accounts.json"),
    ]:
        if src.is_file():
            dst = prof_dir / dst_name
            shutil.copy2(src, dst)
            set_secure_permissions(dst)

    # Update metadata
    info = extract_account_info(
        prof_dir / "antigravity-oauth-token",
        prof_dir / "oauth_creds.json",
        prof_dir / "google_accounts.json",
    )
    meta = {}
    if meta_file.is_file():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta.update({
        "name": curr,
        "email": info.get("email"),
        "name_user": info.get("name"),
        "last_synced": datetime.now(timezone.utc).isoformat(),
        "expiry": info.get("expiry"),
    })
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    set_secure_permissions(meta_file)


# ── CLI Commands ────────────────────────────────────────────────────────────────
def cmd_save(name: Optional[str] = None, email_hint: Optional[str] = None) -> int:
    """Save current runtime credentials as a profile."""
    if not ACTIVE_AGY_TOKEN.is_file() and not ACTIVE_OAUTH_CREDS.is_file():
        print(f"{RED}Error:{RESET} No active Antigravity credentials found in {GEMINI_DIR}.")
        return 1

    curr = get_current_profile_name()
    if curr and name and curr != name:
        sync_active_to_current_profile()

    info = extract_account_info(ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS)
    email = email_hint or info.get("email")

    if not name:
        if email and email != "Unknown":
            name = email.split("@")[0].lower()
            name = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in name)
        else:
            name = f"account_{int(time.time())}"

    prof_dir = PROFILES_DIR / name
    prof_dir.mkdir(parents=True, exist_ok=True)

    for src, dst_name in [
        (ACTIVE_AGY_TOKEN, "antigravity-oauth-token"),
        (ACTIVE_OAUTH_CREDS, "oauth_creds.json"),
        (ACTIVE_GOOGLE_ACCOUNTS, "google_accounts.json"),
    ]:
        if src.is_file():
            dst = prof_dir / dst_name
            shutil.copy2(src, dst)
            set_secure_permissions(dst)

    meta = {
        "name": name,
        "email": email,
        "name_user": info.get("name"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_synced": datetime.now(timezone.utc).isoformat(),
        "expiry": info.get("expiry"),
    }
    meta_file = prof_dir / "profile.json"
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    set_secure_permissions(meta_file)

    set_current_profile_name(name)
    print(f"{GREEN}✓ Saved profile:{RESET} {BOLD}{name}{RESET} ({email})")
    return 0


def cmd_switch(target: str, quiet: bool = False) -> int:
    """Switch active credentials to the specified profile."""
    profiles = list_profiles()
    if not profiles:
        print(f"{RED}Error:{RESET} No profiles saved yet. Run `agy-auth save <name>` first.")
        return 1

    # Resolve by index or name
    matched_profile = None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(profiles):
            matched_profile = profiles[idx]
    else:
        for p in profiles:
            if p.lower() == target.lower():
                matched_profile = p
                break
        if not matched_profile:
            # Partial match
            for p in profiles:
                if target.lower() in p.lower():
                    matched_profile = p
                    break

    if not matched_profile:
        print(f"{RED}Error:{RESET} Profile '{target}' not found. Available profiles: {', '.join(profiles)}")
        return 1

    curr = get_current_profile_name()
    if curr == matched_profile:
        if not quiet:
            print(f"{YELLOW}● Profile '{matched_profile}' is already active.{RESET}")
        # Still make sure files are present
        pass
    else:
        # 1. Sync current active tokens first
        sync_active_to_current_profile()

    # 2. Deploy target profile tokens
    prof_dir = PROFILES_DIR / matched_profile
    AGY_CLI_DIR.mkdir(parents=True, exist_ok=True)
    GEMINI_DIR.mkdir(parents=True, exist_ok=True)

    deployed_count = 0
    for src_name, dst in [
        ("antigravity-oauth-token", ACTIVE_AGY_TOKEN),
        ("oauth_creds.json", ACTIVE_OAUTH_CREDS),
        ("google_accounts.json", ACTIVE_GOOGLE_ACCOUNTS),
    ]:
        src = prof_dir / src_name
        if src.is_file():
            shutil.copy2(src, dst)
            set_secure_permissions(dst)
            deployed_count += 1

    if deployed_count == 0:
        print(f"{RED}Error:{RESET} Profile '{matched_profile}' contains no valid credential files.")
        return 1

    # 3. Update current profile marker
    set_current_profile_name(matched_profile)

    # 4. Clear/invalidate quota cache so status immediately refreshes
    if QUOTA_CACHE.is_file():
        try:
            QUOTA_CACHE.unlink()
        except Exception:
            pass

    # 5. Restart agy-proxy if active
    restart_agy_proxy_if_running()

    info = extract_account_info(ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS)
    if not quiet:
        print(f"{GREEN}✓ Switched to profile:{RESET} {BOLD}{matched_profile}{RESET} ({CYAN}{info.get('email')}{RESET})")
        if info.get("is_expired"):
            print(f"  {YELLOW}⚠ Token is expired or expiring soon. A prompt or keepalive will refresh it.{RESET}")

    return 0


def cmd_toggle() -> int:
    """Toggle between the configured profiles."""
    profiles = list_profiles()
    if len(profiles) < 2:
        print(f"{YELLOW}Notice:{RESET} Need at least 2 profiles to toggle. Configured: {len(profiles)}")
        if profiles:
            return cmd_switch(profiles[0])
        return 1

    curr = get_current_profile_name()
    if not curr or curr not in profiles:
        return cmd_switch(profiles[0])

    # Next profile in ring
    curr_idx = profiles.index(curr)
    next_idx = (curr_idx + 1) % len(profiles)
    return cmd_switch(profiles[next_idx])


def cmd_status(json_output: bool = False) -> int:
    """Display active profile and list all saved profiles."""
    profiles = list_profiles()
    curr = get_current_profile_name()

    active_info = extract_account_info(ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS)

    # Auto-initialize current profile if missing but credentials exist
    if not curr and profiles and active_info.get("email") != "Unknown":
        for p in profiles:
            p_info = extract_account_info(
                PROFILES_DIR / p / "antigravity-oauth-token",
                PROFILES_DIR / p / "oauth_creds.json",
                PROFILES_DIR / p / "google_accounts.json",
            )
            if p_info.get("email") == active_info.get("email"):
                curr = p
                set_current_profile_name(p)
                break

    if json_output:
        profile_data = []
        for p in profiles:
            p_dir = PROFILES_DIR / p
            p_info = extract_account_info(
                p_dir / "antigravity-oauth-token",
                p_dir / "oauth_creds.json",
                p_dir / "google_accounts.json",
                profile_meta_path=p_dir / "profile.json",
            )
            profile_data.append({
                "name": p,
                "email": p_info.get("email"),
                "is_active": (p == curr),
                "expiry": p_info.get("expiry"),
                "is_expired": p_info.get("is_expired"),
            })
        out = {
            "active_profile": curr,
            "active_email": active_info.get("email"),
            "is_expired": active_info.get("is_expired"),
            "expiry": active_info.get("expiry"),
            "profiles": profile_data,
        }
        print(json.dumps(out, indent=2))
        return 0

    print(f"\n{BOLD}═══ Antigravity Subscription Profile Status ═══{RESET}\n")
    if curr:
        print(f"  {BOLD}Active Profile:{RESET}  {GREEN}{curr}{RESET}")
    else:
        print(f"  {BOLD}Active Profile:{RESET}  {YELLOW}[Unmanaged Session]{RESET}")

    print(f"  {BOLD}Account Email:{RESET}   {CYAN}{active_info.get('email')}{RESET}")
    if active_info.get("name"):
        print(f"  {BOLD}User Name:{RESET}       {active_info.get('name')}")

    expiry = active_info.get("expiry")
    if expiry:
        exp_ts = active_info.get("expiry_ts", 0)
        diff = int(exp_ts - time.time())
        if diff > 0:
            hrs = diff // 3600
            mins = (diff % 3600) // 60
            exp_str = f"in {hrs}h {mins}m ({expiry})"
            print(f"  {BOLD}Token Expiry:{RESET}    {GREEN}Valid{RESET} ({exp_str})")
        else:
            print(f"  {BOLD}Token Expiry:{RESET}    {RED}Expired{RESET} ({expiry})")
    else:
        print(f"  {BOLD}Token Expiry:{RESET}    {GRAY}Unknown{RESET}")

    print(f"\n{BOLD}Saved Profiles ({len(profiles)}):{RESET}")
    if not profiles:
        print(f"  {GRAY}(None saved yet. Run `agy-auth save <name>` to save active session.){RESET}\n")
    else:
        for idx, p in enumerate(profiles, 1):
            p_dir = PROFILES_DIR / p
            p_info = extract_account_info(
                p_dir / "antigravity-oauth-token",
                p_dir / "oauth_creds.json",
                p_dir / "google_accounts.json",
                profile_meta_path=p_dir / "profile.json",
            )
            is_active = (p == curr)
            marker = f"{GREEN}* [ACTIVE]{RESET}" if is_active else f"{GRAY}         {RESET}"
            print(f"  [{idx}] {marker} {BOLD}{p:<16}{RESET} {CYAN}{p_info.get('email'):<30}{RESET}")
        print()

    return 0


def cmd_delete(name: str, force: bool = False) -> int:
    """Delete a saved profile."""
    prof_dir = PROFILES_DIR / name
    if not prof_dir.is_dir():
        print(f"{RED}Error:{RESET} Profile '{name}' does not exist.")
        return 1

    curr = get_current_profile_name()
    if curr == name and not force:
        print(f"{RED}Error:{RESET} Profile '{name}' is currently active. Switch to another profile first or pass --force.")
        return 1

    shutil.rmtree(prof_dir)
    if curr == name and CURRENT_PROFILE_FILE.is_file():
        CURRENT_PROFILE_FILE.unlink()

    print(f"{GREEN}✓ Deleted profile:{RESET} {name}")
    return 0


def cmd_login(name: str) -> int:
    """Interactive login flow for onboarding a new account profile."""
    ensure_profiles_dir()
    profiles = list_profiles()

    print(f"\n{BOLD}═══ Antigravity Onboarding: New Profile '{name}' ═══{RESET}\n")

    # 1. Safely archive current active session if not already saved
    curr = get_current_profile_name()
    if not curr and ACTIVE_AGY_TOKEN.is_file():
        active_info = extract_account_info(ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS)
        default_name = "primary"
        if active_info.get("email") != "Unknown":
            default_name = active_info.get("email").split("@")[0].lower()
        print(f"Saving existing active session as backup profile '{default_name}'...")
        cmd_save(default_name)
    else:
        sync_active_to_current_profile()

    # 2. Back up active files to temporary staging
    backup_dir = AGY_CLI_DIR / f".auth_backup_{int(time.time())}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in [ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS]:
        if f.is_file():
            shutil.copy2(f, backup_dir / f.name)

    # 3. Clear active auth tokens to trigger agy login
    for f in [ACTIVE_AGY_TOKEN, ACTIVE_OAUTH_CREDS, ACTIVE_GOOGLE_ACCOUNTS]:
        if f.is_file():
            f.unlink()

    print(f"{YELLOW}Starting `agy` login... Please authenticate the new account in your browser.{RESET}")
    print(f"{GRAY}Command: agy -p \"hello\" --effort low{RESET}\n")

    try:
        # Run agy to trigger login flow
        env = os.environ.copy()
        env["PATH"] = f"{Path.home()}/.local/bin:{env.get('PATH', '')}"
        res = subprocess.run(
            ["agy", "-p", "Authentication check", "--effort", "low"],
            capture_output=False,
            text=True,
            env=env,
        )
        time.sleep(1.0)
    except Exception as e:
        print(f"{RED}Error running agy login:{RESET} {e}")

    # Check if new credentials were created
    if ACTIVE_AGY_TOKEN.is_file() or ACTIVE_OAUTH_CREDS.is_file():
        print(f"\n{GREEN}✓ Login successful! Saving credentials as profile '{name}'...{RESET}")
        cmd_save(name)
        # Clean up backup
        shutil.rmtree(backup_dir, ignore_errors=True)
        return 0
    else:
        print(f"\n{RED}✗ Authentication was not completed.{RESET}")
        print(f"Restoring previous session from backup...")
        for f in backup_dir.iterdir():
            if f.is_file():
                if f.name == "antigravity-oauth-token":
                    shutil.copy2(f, ACTIVE_AGY_TOKEN)
                elif f.name == "oauth_creds.json":
                    shutil.copy2(f, ACTIVE_OAUTH_CREDS)
                elif f.name == "google_accounts.json":
                    shutil.copy2(f, ACTIVE_GOOGLE_ACCOUNTS)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return 1


def cmd_keepalive(quiet: bool = False) -> int:
    """Daily token keepalive daemon.

    Cycles through each configured profile, verifies and refreshes OAuth tokens
    to keep both access and refresh tokens alive and valid, then restores the original profile.
    """
    profiles = list_profiles()
    if not profiles:
        if not quiet:
            print("No profiles configured for keepalive.")
        return 0

    original_profile = get_current_profile_name()
    if not quiet:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting token keepalive for {len(profiles)} profiles...")

    # Save active tokens first
    sync_active_to_current_profile()

    results = {}
    for p in profiles:
        if not quiet:
            print(f"  → Refreshing profile: {BOLD}{p}{RESET}...")
        p_dir = PROFILES_DIR / p
        agy_token_file = p_dir / "antigravity-oauth-token"
        meta_file = p_dir / "profile.json"

        success = False

        # 1. Fast direct OAuth token refresh via Google API
        if agy_token_file.is_file():
            try:
                data = json.loads(agy_token_file.read_text(encoding="utf-8"))
                rt = data.get("token", {}).get("refresh_token")
                if rt:
                    refreshed = refresh_google_oauth_token(rt)
                    if refreshed and refreshed.get("access_token"):
                        new_access = refreshed["access_token"]
                        expires_in = refreshed.get("expires_in", 3599)
                        new_exp_ts = time.time() + float(expires_in)
                        new_exp_iso = datetime.fromtimestamp(new_exp_ts, tz=timezone.utc).isoformat()

                        data["token"]["access_token"] = new_access
                        data["token"]["expiry"] = new_exp_iso
                        agy_token_file.write_text(json.dumps(data), encoding="utf-8")
                        set_secure_permissions(agy_token_file)

                        # If this profile is currently active, also update active runtime token
                        if p == original_profile:
                            ACTIVE_AGY_TOKEN.write_text(json.dumps(data), encoding="utf-8")
                            set_secure_permissions(ACTIVE_AGY_TOKEN)

                        success = True
            except Exception:
                pass

        # 2. Fallback to CLI invocation if direct refresh failed
        if not success:
            cmd_switch(p, quiet=True)
            try:
                env = os.environ.copy()
                env["PATH"] = f"{Path.home()}/.local/bin:{env.get('PATH', '')}"
                proc = subprocess.run(
                    ["agy", "-p", "echo keepalive", "--effort", "low", "--disable-slash-commands"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                )
                success = (proc.returncode == 0)
            except Exception as e:
                if not quiet:
                    print(f"    {RED}Keepalive error for {p}:{RESET} {e}")
            sync_active_to_current_profile()

        # Update metadata
        meta = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        meta["last_keepalive"] = datetime.now(timezone.utc).isoformat()
        meta["keepalive_success"] = success
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        results[p] = success
        if not quiet:
            status_str = f"{GREEN}OK{RESET}" if success else f"{RED}FAILED{RESET}"
            print(f"    Result: {status_str}")

    # Restore original profile
    if original_profile and original_profile in profiles:
        cmd_switch(original_profile, quiet=True)
    elif profiles:
        cmd_switch(profiles[0], quiet=True)

    if not quiet:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Keepalive finished: {results}")

    return 0 if all(results.values()) else 1


def restart_agy_proxy_if_running() -> None:
    """If agy-proxy systemd user service is running, restart it to reload credentials."""
    try:
        check = subprocess.run(
            ["systemctl", "--user", "is-active", "agy-proxy.service"],
            capture_output=True,
            text=True,
        )
        if check.returncode == 0 and "active" in check.stdout:
            subprocess.run(
                ["systemctl", "--user", "restart", "agy-proxy.service"],
                capture_output=True,
            )
    except Exception:
        pass


# ── Main Entrypoint ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity (agy) Multi-Account Profile Switcher & Keepalive Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # status
    p_status = subparsers.add_parser("status", aliases=["whoami", "current"], help="Show active profile and saved accounts")
    p_status.add_argument("--json", action="store_true", help="Output in JSON format")

    # list
    p_list = subparsers.add_parser("list", aliases=["ls"], help="List all saved profiles")
    p_list.add_argument("--json", action="store_true", help="Output in JSON format")

    # save
    p_save = subparsers.add_parser("save", help="Save current active session as a named profile")
    p_save.add_argument("name", nargs="?", default=None, help="Name of the profile (defaults to username from email)")
    p_save.add_argument("--email", help="Explicit email override")

    # switch
    p_switch = subparsers.add_parser("switch", aliases=["use", "select"], help="Switch active credentials to a profile")
    p_switch.add_argument("target", help="Profile name or index number (e.g. 1 or 2)")
    p_switch.add_argument("-q", "--quiet", action="store_true", help="Suppress non-error output")

    # toggle
    subparsers.add_parser("toggle", aliases=["next"], help="Toggle between configured profiles")

    # login
    p_login = subparsers.add_parser("login", aliases=["add"], help="Interactive login flow to onboard a new account")
    p_login.add_argument("name", help="Name for the new profile (e.g. work, personal, user2)")

    # delete
    p_delete = subparsers.add_parser("delete", aliases=["rm"], help="Delete a saved profile")
    p_delete.add_argument("name", help="Profile name to remove")
    p_delete.add_argument("-f", "--force", action="store_true", help="Force deletion even if active")

    # keepalive
    p_keep = subparsers.add_parser("keepalive", aliases=["refresh-all"], help="Daily token refresh keepalive daemon")
    p_keep.add_argument("-q", "--quiet", action="store_true", help="Quiet mode for background cron/timers")

    args = parser.parse_args()

    ensure_profiles_dir()

    if not args.subcommand or args.subcommand in ("status", "whoami", "current"):
        return cmd_status(json_output=getattr(args, "json", False))
    elif args.subcommand in ("list", "ls"):
        return cmd_status(json_output=getattr(args, "json", False))
    elif args.subcommand == "save":
        return cmd_save(name=args.name, email_hint=getattr(args, "email", None))
    elif args.subcommand in ("switch", "use", "select"):
        return cmd_switch(target=args.target, quiet=args.quiet)
    elif args.subcommand in ("toggle", "next"):
        return cmd_toggle()
    elif args.subcommand in ("login", "add"):
        return cmd_login(name=args.name)
    elif args.subcommand in ("delete", "rm"):
        return cmd_delete(name=args.name, force=args.force)
    elif args.subcommand in ("keepalive", "refresh-all"):
        return cmd_keepalive(quiet=args.quiet)

    return 0


if __name__ == "__main__":
    sys.exit(main())
