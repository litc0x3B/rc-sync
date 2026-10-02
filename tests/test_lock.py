import multiprocessing
import os
import threading

from rc_sync.lock import ProcessLock


def _hold_lock(lock_path_str, acquired_event, release_event):
    from pathlib import Path
    lock = ProcessLock(Path(lock_path_str))
    lock.acquire()
    acquired_event.set()
    release_event.wait(timeout=5)
    lock.release()


def test_lock_acquire_and_release(tmp_path, capsys):
    lock_file = tmp_path / "test.lock"
    lock = ProcessLock(lock_file)

    with lock:
        assert lock_file.exists()
        content = lock_file.read_text()
        assert str(os.getpid()) in content

    captured = capsys.readouterr()
    assert f"Lock acquired by PID {os.getpid()}." in captured.out
    assert "Lock released." in captured.out


def test_lock_contention(tmp_path, capsys):
    lock_file = tmp_path / "test_contention.lock"

    acquired_event = multiprocessing.Event()
    release_event = multiprocessing.Event()

    p = multiprocessing.Process(
        target=_hold_lock,
        args=(str(lock_file), acquired_event, release_event),
    )
    p.start()

    # Ensure child has actually acquired the lock
    assert acquired_event.wait(timeout=5)

    # Release child's lock shortly after parent begins waiting
    timer = threading.Timer(0.2, release_event.set)
    timer.start()

    try:
        lock2 = ProcessLock(lock_file)
        lock2.acquire()
        lock2.release()
    finally:
        timer.cancel()
        release_event.set()
        p.join()

    captured = capsys.readouterr()
    assert "Waiting for release..." in captured.out
    assert f"Lock acquired by PID {os.getpid()}." in captured.out
    assert "Lock released." in captured.out
