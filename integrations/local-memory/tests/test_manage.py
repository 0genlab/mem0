import importlib.util
import json
import stat
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("local_memory_manage", Path(__file__).parents[1] / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


def test_init_preserves_configuration_and_keeps_credentials_private(tmp_path):
    path = tmp_path / "config/config.json"
    data = tmp_path / "data"
    original = manage.initialize(path, data, 8769)
    token = Path(original["token_file"]).read_bytes()
    assert len(token) >= 32
    assert stat.S_IMODE(Path(original["token_file"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "AIHUBMIX_API_KEY" not in path.read_text()
    assert manage.initialize(path, tmp_path / "different", 12345) == original
    assert Path(original["token_file"]).read_bytes() == token


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:8769",
        "http://127.0.0.1:8769@evil.example",
        "http://localhost:8769",
        "http://127.0.0.1:8769/path",
        "http://127.0.0.1:8769?redirect=external",
    ],
)
def test_nonlocal_and_ambiguous_urls_are_rejected(tmp_path, url):
    path = tmp_path / "config.json"
    config = manage.initialize(path, tmp_path / "data", 8769)
    config["url"] = url
    manage.write_private(path, json.dumps(config).encode())
    with pytest.raises(ValueError):
        manage.read_config(path)


def test_launch_agent_uses_existing_environment_without_storing_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(manage.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AIHUBMIX_API_KEY", "unit-test-secret")
    path = tmp_path / "config/config.json"
    config = manage.initialize(path, tmp_path / "data", 8769)
    plist_path = manage.install_service(path, config)
    value = manage.plistlib.loads(plist_path.read_bytes())
    assert value["ProgramArguments"][0:2] == ["/bin/zsh", "-lic"]
    assert value["Umask"] == 0o077
    assert "unit-test-secret" not in plist_path.read_text()
    assert str(path) in value["ProgramArguments"][2]


def test_init_restricts_existing_storage_and_rejects_symlinks(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    manage.initialize(tmp_path / "config/config.json", data, 8769)
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    link = tmp_path / "linked-data"
    link.symlink_to(data, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        manage.initialize(tmp_path / "another/config.json", link, 8769)
