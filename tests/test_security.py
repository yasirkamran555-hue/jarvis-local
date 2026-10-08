import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import security
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from replit_mcp import _VaultTokenStorage


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env"
        self.vault_file = self.root / "connections.json"
        self.env_patch = patch.multiple(security, ENV_FILE=self.env_file, VAULT_FILE=self.vault_file)
        self.env_patch.start()
        os.environ.pop("JARVIS_FERNET_KEY", None)

    def tearDown(self):
        os.environ.pop("JARVIS_FERNET_KEY", None)
        self.env_patch.stop()
        self.temp.cleanup()

    def test_vault_is_encrypted_and_password_is_hidden_from_listing(self):
        row = security.save_connection({
            "name": "Test", "type": "github", "host": "example/repo",
            "username": "user", "password": "private-token",
        })
        self.assertNotIn("password", row)
        self.assertNotIn(b"private-token", self.vault_file.read_bytes())
        self.assertNotIn("private-token", str(security.list_connections()))
        self.assertEqual(security.get_connection(row["id"])["password"], "private-token")

    def test_disable_prevents_normal_use_and_delete_removes_entry(self):
        row = security.save_connection({
            "name": "Test", "type": "github", "host": "example/repo",
            "username": "user", "password": "private-token",
        })
        security.set_connection_enabled(row["id"], False)
        with self.assertRaises(PermissionError):
            security.get_connection(row["id"])
        security.delete_connection(row["id"])
        self.assertEqual(security.list_connections(), [])

    def test_wrong_key_does_not_overwrite_vault(self):
        row = security.save_connection({
            "name": "Test", "type": "github", "host": "example/repo",
            "username": "user", "password": "private-token",
        })
        previous = self.vault_file.read_bytes()
        os.environ["JARVIS_FERNET_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        with self.assertRaises(RuntimeError):
            security.get_connection(row["id"])
        self.assertEqual(self.vault_file.read_bytes(), previous)

    def test_replit_oauth_state_is_encrypted_and_hidden_from_connection_list(self):
        secret = "replit-oauth-refresh-secret"
        security.set_integration_state("replit_mcp", {
            "tokens": {"refresh_token": secret},
            "client_info": {"client_id": "local-client"},
        })
        raw_vault = self.vault_file.read_bytes()
        self.assertNotIn(secret.encode(), raw_vault)
        self.assertEqual(security.get_integration_state("replit_mcp")["tokens"]["refresh_token"], secret)
        self.assertEqual(security.list_connections(), [])
        security.delete_integration_state("replit_mcp")
        self.assertEqual(security.get_integration_state("replit_mcp"), {})

    def test_replit_oauth_sdk_models_round_trip_through_encrypted_vault(self):
        import asyncio

        storage = _VaultTokenStorage()
        token = OAuthToken(access_token="access-secret", refresh_token="refresh-secret")
        client_info = OAuthClientInformationFull(
            client_id="local-oauth-client",
            redirect_uris=[AnyUrl("http://127.0.0.1:8765/oauth/callback")],
        )

        async def save_and_load():
            await storage.set_tokens(token)
            await storage.set_client_info(client_info)
            return await storage.get_tokens(), await storage.get_client_info()

        loaded_token, loaded_client = asyncio.run(save_and_load())
        self.assertEqual(loaded_token.refresh_token, "refresh-secret")
        self.assertEqual(loaded_client.client_id, "local-oauth-client")
        self.assertNotIn(b"refresh-secret", self.vault_file.read_bytes())


if __name__ == "__main__":
    unittest.main()
