import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace

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


def test_two_service_labels_are_isolated_and_legacy_config_works(tmp_path, monkeypatch):
    monkeypatch.setattr(manage.Path, "home", lambda: tmp_path)
    old_path = tmp_path / "work/config.json"
    personal_path = tmp_path / "personal/config.json"
    old = manage.initialize(old_path, tmp_path / "work-data", 8769)
    personal = manage.initialize(personal_path, tmp_path / "personal-data", 8770, "com.0genlab.mem0-local.personal")
    assert "service_label" not in manage.read_config(old_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(manage.subprocess, "run", run)
    for path, config, label in (
        (old_path, old, manage.LABEL),
        (personal_path, personal, "com.0genlab.mem0-local.personal"),
    ):
        target = manage.install_service(path, config)
        assert target.name == f"{label}.plist"
        assert manage.plistlib.loads(target.read_bytes())["Label"] == label
        manage.start_service(path, config)
        assert calls[-2][2].endswith("/" + label)
        assert calls[-1][-1].endswith("/" + label)
    assert old["token_file"] != personal["token_file"]
    assert old["data_dir"] != personal["data_dir"]


@pytest.mark.parametrize("label", ["../../bad", "bad/label", "bad space", "bad\nlabel", "", "a" * 129, 42])
def test_reject_invalid_service_label(tmp_path, label):
    path = tmp_path / "config/config.json"
    config = manage.initialize(path, tmp_path / "data", 8769)
    config["service_label"] = label
    manage.write_private(path, json.dumps(config).encode())
    with pytest.raises(ValueError, match="service_label"):
        manage.read_config(path)
