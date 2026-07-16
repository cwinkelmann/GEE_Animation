import json

from gee_animation import auth


class _FakeEE:
    def __init__(self):
        self.rec = {}
    def ServiceAccountCredentials(self, email, key_file):
        self.rec["sa"] = (email, key_file)
        return "CREDS"
    def Initialize(self, credentials=None, project=None):
        self.rec["init"] = (credentials, project)
    def Authenticate(self):
        self.rec["auth"] = True


def test_init_uses_service_account_from_env(tmp_path, monkeypatch):
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"client_email": "sa@proj.iam.gserviceaccount.com"}))
    monkeypatch.setenv("EE_SERVICE_ACCOUNT_KEY", str(key))
    monkeypatch.delenv("EE_SERVICE_ACCOUNT", raising=False)
    ee = _FakeEE()
    auth.init("proj", ee_module=ee)
    # email read from the key file, credentials + project passed to Initialize, no interactive auth
    assert ee.rec["sa"] == ("sa@proj.iam.gserviceaccount.com", str(key))
    assert ee.rec["init"] == ("CREDS", "proj")
    assert "auth" not in ee.rec


def test_init_uses_explicit_service_account_email(tmp_path, monkeypatch):
    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setenv("EE_SERVICE_ACCOUNT_KEY", str(key))
    monkeypatch.setenv("EE_SERVICE_ACCOUNT", "explicit@x.iam.gserviceaccount.com")
    ee = _FakeEE()
    auth.init("proj", ee_module=ee)
    assert ee.rec["sa"][0] == "explicit@x.iam.gserviceaccount.com"


def test_init_falls_back_to_interactive_without_service_account(monkeypatch):
    monkeypatch.delenv("EE_SERVICE_ACCOUNT_KEY", raising=False)
    ee = _FakeEE()
    auth.init("proj", ee_module=ee)
    assert ee.rec["init"] == (None, "proj")   # Initialize(project=...) only
    assert "sa" not in ee.rec and "auth" not in ee.rec
