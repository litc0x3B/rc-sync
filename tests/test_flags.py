from rc_sync.rclone import combine_flags, expand_path, interpolate_flags


def test_interpolate_flags():
    assert interpolate_flags("--max-lock %tm", 5) == "--max-lock 5m"
    assert interpolate_flags("100%%", 10) == "100%"
    assert interpolate_flags("100%%%tm", 10) == "100%10m"
    assert interpolate_flags("--flag %t --other %%", 7) == "--flag 7 --other %"
    assert interpolate_flags("", 5) == ""


def test_expand_path():
    assert expand_path("~/Documents").startswith("/")
    assert not expand_path("~/Documents").startswith("~")
    assert expand_path("remote:Docs") == "remote:Docs"
    assert expand_path("drive:/folder") == "drive:/folder"
    assert expand_path("/absolute/path") == "/absolute/path"


def test_combine_flags_cli_override():
    # When --override-flags is passed, ONLY CLI extra flags are used
    flags = combine_flags(
        cli_override=True,
        cli_extra_flags="--dry-run -v",
        is_init=False,
        global_flags="--resilient --recover --max-lock %tm",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=False,
        mapping_extra_flags_init="",
        mapping_override_flags_init=False,
        sync_freq_minutes=5,
    )
    assert flags == ["--dry-run", "-v"]


def test_combine_flags_init_without_override():
    # Initial resync: init_flags + flags + "--resync" + CLI extra_flags
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_flags="--resilient --max-lock %tm",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=False,
        mapping_extra_flags_init="--backup-dir old",
        mapping_override_flags_init=False,
        sync_freq_minutes=5,
        global_flags_init="--global-init",
    )
    assert flags == [
        "--global-init",
        "--backup-dir",
        "old",
        "--resilient",
        "--max-lock",
        "5m",
        "--fast-list",
        "--resync",
        "--verbose",
    ]


def test_combine_flags_init_default_empty_global():
    # Initial resync with default empty global_flags_init
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_flags="--resilient",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=False,
        mapping_extra_flags_init="--backup-dir old",
        mapping_override_flags_init=False,
        sync_freq_minutes=5,
        global_flags_init="",
    )
    assert flags == ["--backup-dir", "old", "--resilient", "--fast-list", "--resync", "--verbose"]


def test_combine_flags_init_with_override():
    # Initial resync with mapping override_flags_init:
    # global_flags_init is ignored, but regular flags are included
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_flags="--resilient",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=False,
        mapping_extra_flags_init="--custom-init",
        mapping_override_flags_init=True,
        sync_freq_minutes=5,
        global_flags_init="--global-init-ignored",
    )
    assert flags == ["--custom-init", "--resilient", "--fast-list", "--resync", "--verbose"]


def test_combine_flags_init_with_both_overrides():
    # Initial resync with both override_flags_init and override_flags:
    # Both global_flags_init and global_flags are ignored
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_flags="--global-ignored",
        mapping_extra_flags="--custom-reg",
        mapping_override_flags=True,
        mapping_extra_flags_init="--custom-init",
        mapping_override_flags_init=True,
        sync_freq_minutes=5,
        global_flags_init="--global-init-ignored",
    )
    assert flags == ["--custom-init", "--custom-reg", "--resync", "--verbose"]


def test_combine_flags_regular_without_override():
    # Regular sync: Global flags + Mapping extra_flags + CLI extra_flags
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--dry-run",
        is_init=False,
        global_flags="--resilient --max-lock %tm",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=False,
        mapping_extra_flags_init="--ignored",
        mapping_override_flags_init=False,
        sync_freq_minutes=10,
    )
    assert flags == ["--resilient", "--max-lock", "10m", "--fast-list", "--dry-run"]


def test_combine_flags_regular_with_override():
    # Regular sync with mapping override: Mapping extra_flags + CLI extra_flags (global ignored)
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--dry-run",
        is_init=False,
        global_flags="--resilient --max-lock %tm",
        mapping_extra_flags="--only-this",
        mapping_override_flags=True,
        mapping_extra_flags_init="",
        mapping_override_flags_init=False,
        sync_freq_minutes=10,
    )
    assert flags == ["--only-this", "--dry-run"]
