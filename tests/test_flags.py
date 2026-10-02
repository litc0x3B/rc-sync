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
        global_extra_flags="--resilient --recover --max-lock %tm",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=None,
        mapping_extra_flags_init="",
        mapping_override_flags_init=None,
        sync_freq_minutes=5,
    )
    assert flags == ["--dry-run", "-v"]


def test_combine_flags_init_without_override():
    # Initial resync without override: Global ExtraFlags + Mapping ExtraFlagsInit + CLI extraFlags
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_extra_flags="--resilient --recover --max-lock %tm",
        mapping_extra_flags="--ignored",
        mapping_override_flags=None,
        mapping_extra_flags_init="--backup-dir old",
        mapping_override_flags_init=None,
        sync_freq_minutes=5,
    )
    assert flags == [
        "--resilient",
        "--recover",
        "--max-lock",
        "5m",
        "--backup-dir",
        "old",
        "--verbose",
    ]


def test_combine_flags_init_with_override():
    # Initial resync with override: OverrideFlagsInit + CLI extraFlags (Global ignored)
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--verbose",
        is_init=True,
        global_extra_flags="--resilient --recover --max-lock %tm",
        mapping_extra_flags="--ignored",
        mapping_override_flags=None,
        mapping_extra_flags_init="",
        mapping_override_flags_init="--custom-init",
        sync_freq_minutes=5,
    )
    assert flags == ["--custom-init", "--verbose"]


def test_combine_flags_regular_without_override():
    # Regular sync: Global ExtraFlags + Mapping ExtraFlags + CLI extraFlags
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--dry-run",
        is_init=False,
        global_extra_flags="--resilient --max-lock %tm",
        mapping_extra_flags="--fast-list",
        mapping_override_flags=None,
        mapping_extra_flags_init="--ignored",
        mapping_override_flags_init=None,
        sync_freq_minutes=10,
    )
    assert flags == ["--resilient", "--max-lock", "10m", "--fast-list", "--dry-run"]


def test_combine_flags_regular_with_override():
    # Regular sync with mapping override: OverrideFlags + CLI extraFlags (Global ignored)
    flags = combine_flags(
        cli_override=False,
        cli_extra_flags="--dry-run",
        is_init=False,
        global_extra_flags="--resilient --max-lock %tm",
        mapping_extra_flags="",
        mapping_override_flags="--only-this",
        mapping_extra_flags_init="",
        mapping_override_flags_init=None,
        sync_freq_minutes=10,
    )
    assert flags == ["--only-this", "--dry-run"]
