from rc_sync.state import MappingStatus, StateManager


def test_state_manager_initial_state(tmp_path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)

    st = manager.get_state("~/Docs", "remote:Docs")
    assert st.path1 == "~/Docs"
    assert st.path2 == "remote:Docs"
    assert st.init_success is False
    assert st.last_sync_time is None
    assert st.status == MappingStatus.INIT_PENDING


def test_state_manager_update_and_save(tmp_path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)

    manager.update_status(
        "~/Docs",
        "remote:Docs",
        status=MappingStatus.INIT_SUCCESS,
        init_success=True,
    )
    st = manager.get_state("~/Docs", "remote:Docs")
    assert st.status == MappingStatus.INIT_SUCCESS
    assert st.init_success is True
    assert st.last_sync_time is not None

    manager.save()
    assert state_file.exists()

    # Load in new manager
    manager2 = StateManager(state_file)
    st2 = manager2.get_state("~/Docs", "remote:Docs")
    assert st2.status == MappingStatus.INIT_SUCCESS
    assert st2.init_success is True
    assert st2.last_sync_time == st.last_sync_time


def test_state_manager_never_deletes_entries(tmp_path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)
    manager.update_status("p1", "p2", MappingStatus.SYNC_SUCCESS)
    manager.update_status("p3", "p4", MappingStatus.INIT_FAILED)
    manager.save()

    manager2 = StateManager(state_file)
    all_s = manager2.all_states()
    assert len(all_s) == 2
    keys = {(s.path1, s.path2) for s in all_s}
    assert ("p1", "p2") in keys
    assert ("p3", "p4") in keys


def test_state_manager_reset_and_remove(tmp_path):
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)
    manager.update_status("~/Docs", "remote:Docs", MappingStatus.SYNC_SUCCESS, init_success=True)
    manager.update_status("/other/p1", "/other/p2", MappingStatus.INIT_SUCCESS, init_success=True)
    manager.save()

    # Reset ~/Docs
    manager.reset_state("~/Docs", "remote:Docs")
    st = manager.get_state("~/Docs", "remote:Docs")
    assert st.status == MappingStatus.INIT_PENDING
    assert st.init_success is False
    assert st.last_sync_time is None

    # Other mapping remains untouched
    st2 = manager.get_state("/other/p1", "/other/p2")
    assert st2.status == MappingStatus.INIT_SUCCESS
    assert st2.init_success is True

    # Remove /other/p1
    assert manager.remove_state("/other/p1", "/other/p2") is True
    assert len(manager.all_states()) == 1

    # Reset all
    manager.update_status("~/Docs", "remote:Docs", MappingStatus.SYNC_SUCCESS, init_success=True)
    manager.reset_all()
    assert manager.get_state("~/Docs", "remote:Docs").init_success is False
    assert manager.get_state("~/Docs", "remote:Docs").status == MappingStatus.INIT_PENDING

    # Clear all
    manager.clear_all()
    assert len(manager.all_states()) == 0

