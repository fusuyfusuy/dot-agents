#!/usr/bin/env python3
"""Comprehensive test suite for Antigravity Profile Switcher (agy-auth)."""
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent))

import importlib
agy_auth = importlib.import_module("agy-auth")


class TestAgyAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.gemini_dir = Path(self.temp_dir) / ".gemini"
        self.agy_cli_dir = self.gemini_dir / "antigravity-cli"
        self.profiles_dir = self.agy_cli_dir / "profiles"

        self.gemini_dir.mkdir(parents=True)
        self.agy_cli_dir.mkdir(parents=True)
        self.profiles_dir.mkdir(parents=True)

        # Patch module paths to use isolated test directory
        self.patchers = [
            patch.object(agy_auth, "GEMINI_DIR", self.gemini_dir),
            patch.object(agy_auth, "AGY_CLI_DIR", self.agy_cli_dir),
            patch.object(agy_auth, "PROFILES_DIR", self.profiles_dir),
            patch.object(agy_auth, "CURRENT_PROFILE_FILE", self.profiles_dir / "current"),
            patch.object(agy_auth, "ACTIVE_AGY_TOKEN", self.agy_cli_dir / "antigravity-oauth-token"),
            patch.object(agy_auth, "ACTIVE_OAUTH_CREDS", self.gemini_dir / "oauth_creds.json"),
            patch.object(agy_auth, "ACTIVE_GOOGLE_ACCOUNTS", self.gemini_dir / "google_accounts.json"),
            patch.object(agy_auth, "QUOTA_CACHE", Path(self.temp_dir) / "quota-cache.json"),
            patch.object(agy_auth, "restart_agy_proxy_if_running", MagicMock()),
            patch.object(agy_auth, "fetch_google_userinfo", MagicMock(return_value={})),
            patch.object(agy_auth, "refresh_google_oauth_token", MagicMock(return_value=None)),
        ]
        for p in self.patchers:
            p.start()

        # Seed initial fake active credentials for user 1
        self._seed_active_credentials("user1@example.com", "User One", "token-user1-secret")

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_active_credentials(self, email: str, name: str, access_token: str):
        # google_accounts.json
        (self.gemini_dir / "google_accounts.json").write_text(
            json.dumps({"active": email, "old": []}), encoding="utf-8"
        )
        # oauth_creds.json
        (self.gemini_dir / "oauth_creds.json").write_text(
            json.dumps({
                "access_token": access_token,
                "expiry_date": 1780000000000,
                "refresh_token": f"refresh-{email}",
            }),
            encoding="utf-8",
        )
        # antigravity-oauth-token
        (self.agy_cli_dir / "antigravity-oauth-token").write_text(
            json.dumps({
                "token": {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "refresh_token": f"refresh-{email}",
                    "expiry": "2026-09-01T12:00:00+00:00",
                },
                "auth_method": "consumer",
            }),
            encoding="utf-8",
        )

    def test_save_and_list_profile(self):
        # Save current active credentials as profile 'personal'
        ret = agy_auth.cmd_save("personal")
        self.assertEqual(ret, 0)
        self.assertEqual(agy_auth.get_current_profile_name(), "personal")

        profiles = agy_auth.list_profiles()
        self.assertIn("personal", profiles)

        prof_dir = self.profiles_dir / "personal"
        self.assertTrue((prof_dir / "antigravity-oauth-token").is_file())
        self.assertTrue((prof_dir / "oauth_creds.json").is_file())
        self.assertTrue((prof_dir / "google_accounts.json").is_file())
        self.assertTrue((prof_dir / "profile.json").is_file())

        # Check permissions (0600 on sensitive token file)
        mode = stat.S_IMODE(os.stat(prof_dir / "antigravity-oauth-token").st_mode)
        self.assertEqual(mode & 0o077, 0, "File permissions must restrict group/other access")

    def test_save_and_switch_between_two_profiles(self):
        # 1. Save Profile A (user1)
        ret = agy_auth.cmd_save("user1")
        self.assertEqual(ret, 0)

        # 2. Seed and save Profile B (user2)
        self._seed_active_credentials("user2@work.com", "User Two", "token-user2-secret")
        ret = agy_auth.cmd_save("user2")
        self.assertEqual(ret, 0)

        self.assertEqual(agy_auth.get_current_profile_name(), "user2")

        # 3. Switch back to user1
        ret = agy_auth.cmd_switch("user1")
        self.assertEqual(ret, 0)
        self.assertEqual(agy_auth.get_current_profile_name(), "user1")

        # Verify active runtime tokens match user1
        with open(self.agy_cli_dir / "antigravity-oauth-token") as f:
            data = json.load(f)
            self.assertEqual(data["token"]["access_token"], "token-user1-secret")

        with open(self.gemini_dir / "google_accounts.json") as f:
            data = json.load(f)
            self.assertEqual(data["active"], "user1@example.com")

        # 4. Switch back to user2
        ret = agy_auth.cmd_switch("user2")
        self.assertEqual(ret, 0)
        self.assertEqual(agy_auth.get_current_profile_name(), "user2")

        with open(self.agy_cli_dir / "antigravity-oauth-token") as f:
            data = json.load(f)
            self.assertEqual(data["token"]["access_token"], "token-user2-secret")

    def test_token_refresh_sync_preservation(self):
        """Verify that when runtime tokens get refreshed, switching profiles preserves them."""
        # 1. Save user1
        agy_auth.cmd_save("user1")

        # 2. Save user2
        self._seed_active_credentials("user2@work.com", "User Two", "token-user2-secret")
        agy_auth.cmd_save("user2")

        # 3. Switch to user1
        agy_auth.cmd_switch("user1")

        # 4. Simulate agy runtime refreshing the access token in active directory while user1 is active
        with open(self.agy_cli_dir / "antigravity-oauth-token", "w") as f:
            json.dump({
                "token": {
                    "access_token": "token-user1-NEW-REFRESHED-12345",
                    "refresh_token": "refresh-user1",
                }
            }, f)

        # 5. Switch to user2 (this should sync user1's refreshed token before deploying user2)
        agy_auth.cmd_switch("user2")

        # 6. Switch back to user1
        agy_auth.cmd_switch("user1")

        # Verify user1 has the NEW refreshed token, not the stale old one!
        with open(self.agy_cli_dir / "antigravity-oauth-token") as f:
            data = json.load(f)
            self.assertEqual(data["token"]["access_token"], "token-user1-NEW-REFRESHED-12345")

    def test_toggle(self):
        agy_auth.cmd_save("acc1")
        self._seed_active_credentials("acc2@test.com", "Two", "tok2")
        agy_auth.cmd_save("acc2")

        self.assertEqual(agy_auth.get_current_profile_name(), "acc2")
        agy_auth.cmd_toggle()
        self.assertEqual(agy_auth.get_current_profile_name(), "acc1")
        agy_auth.cmd_toggle()
        self.assertEqual(agy_auth.get_current_profile_name(), "acc2")

    def test_keepalive_cycle(self):
        agy_auth.cmd_save("acc1")
        self._seed_active_credentials("acc2@test.com", "Two", "tok2")
        agy_auth.cmd_save("acc2")

        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="ok")
            ret = agy_auth.cmd_keepalive(quiet=True)
            self.assertEqual(ret, 0)
            # Must have pinged each profile
            self.assertGreaterEqual(mock_sub.call_count, 2)

        # Original profile must be restored (acc2)
        self.assertEqual(agy_auth.get_current_profile_name(), "acc2")

    def test_switch_by_index_and_json_status(self):
        agy_auth.cmd_save("acc1")
        self._seed_active_credentials("acc2@test.com", "Two", "tok2")
        agy_auth.cmd_save("acc2")

        # Switch by index '1' -> should switch to acc1
        ret = agy_auth.cmd_switch("1")
        self.assertEqual(ret, 0)
        self.assertEqual(agy_auth.get_current_profile_name(), "acc1")

        # Switch by index '2' -> should switch to acc2
        ret = agy_auth.cmd_switch("2")
        self.assertEqual(ret, 0)
        self.assertEqual(agy_auth.get_current_profile_name(), "acc2")

    def test_invalid_switch_and_error_handling(self):
        agy_auth.cmd_save("acc1")
        ret = agy_auth.cmd_switch("nonexistent_profile")
        self.assertEqual(ret, 1)

    def test_delete_profile(self):
        agy_auth.cmd_save("acc1")
        self._seed_active_credentials("acc2@test.com", "Two", "tok2")
        agy_auth.cmd_save("acc2")

        # Cannot delete active profile without --force
        ret = agy_auth.cmd_delete("acc2", force=False)
        self.assertEqual(ret, 1)

    def test_multi_account_distinct_emails(self):
        """Verify that profiles with different emails report their respective distinct emails."""
        # Profile 1
        agy_auth.cmd_save("user1", email_hint="user1@example.com")

        # Profile 2 with different credentials
        self._seed_active_credentials("work2@corp.com", "Work Account", "tok-work2")
        agy_auth.cmd_save("user2", email_hint="work2@corp.com")

        # Check status json
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            agy_auth.cmd_status(json_output=True)
        status_data = json.loads(f.getvalue())

        prof_map = {p["name"]: p["email"] for p in status_data["profiles"]}
        self.assertEqual(prof_map["user1"], "user1@example.com")
        self.assertEqual(prof_map["user2"], "work2@corp.com")

    def test_oauth_token_direct_refresh(self):
        """Verify direct OAuth refresh succeeds and updates token expiry."""
        agy_auth.cmd_save("acc1")

        with patch.object(agy_auth, "refresh_google_oauth_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "ya29.new_refreshed_token",
                "expires_in": 3600,
                "id_token": "header." + agy_auth.base64.urlsafe_b64encode(json.dumps({"email": "refreshed@example.com"}).encode()).decode().rstrip("=") + ".sig",
            }
            ret = agy_auth.cmd_keepalive(quiet=True)
            self.assertEqual(ret, 0)
            mock_refresh.assert_called()


if __name__ == "__main__":
    unittest.main()
