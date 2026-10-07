import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import security


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


if __name__ == "__main__":
    unittest.main()
