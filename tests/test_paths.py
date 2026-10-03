from pathlib import Path

from rc_sync.paths import _is_safe_installed_schema, get_installed_schema_path


def test_is_safe_installed_schema(tmp_path):
    safe_file = tmp_path / "schema.json"
    safe_file.write_text("{}")
    assert _is_safe_installed_schema(safe_file) is True

    non_existent = tmp_path / "does_not_exist.json"
    assert _is_safe_installed_schema(non_existent) is False

    nix_store_fake = Path("/nix/store/abcdef-pkg/share/rc-sync/schema.json")
    assert _is_safe_installed_schema(nix_store_fake) is False


def test_get_installed_schema_path_from_xdg_data_dirs(tmp_path, monkeypatch):
    share_dir = tmp_path / "usr" / "share"
    schema_file = share_dir / "rc-sync" / "schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("{}")

    monkeypatch.setenv("XDG_DATA_DIRS", str(share_dir))
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    found = get_installed_schema_path()
    assert found == schema_file


def test_get_installed_schema_path_ignores_nix_store(tmp_path, monkeypatch):
    nix_share = tmp_path / "nix" / "store" / "pkg" / "share"
    schema_file = nix_share / "rc-sync" / "schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("{}")

    monkeypatch.setenv("XDG_DATA_DIRS", f"/nix/store/pkg/share:{tmp_path}/empty")
    monkeypatch.setattr("shutil.which", lambda _cmd: "/nix/store/pkg/bin/rc-sync")

    found = get_installed_schema_path()
    assert found is None


def test_get_installed_schema_path_from_doc_dir(tmp_path, monkeypatch):
    profile_share = tmp_path / "profile" / "share"
    doc_schema = profile_share / "doc" / "rc-sync" / "schema.json"
    doc_schema.parent.mkdir(parents=True)
    doc_schema.write_text("{}")

    fake_bin = tmp_path / "profile" / "bin" / "rc-sync"
    monkeypatch.setattr("shutil.which", lambda _cmd: str(fake_bin))

    found = get_installed_schema_path()
    assert found == doc_schema


def test_get_installed_schema_path_from_xdg_data_home_priority(tmp_path, monkeypatch):
    user_share = tmp_path / "user_data_home"
    user_schema = user_share / "rc-sync" / "schema.json"
    user_schema.parent.mkdir(parents=True)
    user_schema.write_text('{"source": "user"}')

    sys_share = tmp_path / "sys_data_dirs"
    sys_schema = sys_share / "rc-sync" / "schema.json"
    sys_schema.parent.mkdir(parents=True)
    sys_schema.write_text('{"source": "system"}')

    monkeypatch.setenv("XDG_DATA_HOME", str(user_share))
    monkeypatch.setenv("XDG_DATA_DIRS", str(sys_share))
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    found = get_installed_schema_path()
    assert found == user_schema
