import re

from rc_sync.logger import setup_logger, stream_rclone_line


def test_logger_format_and_split(capsys):
    logger = setup_logger()

    logger.info("Test info message", extra={"context": "sync"})
    logger.success("Test success message", extra={"context": "alias:docs"})
    logger.warning("Test warning message", extra={"context": "daemon"})
    logger.error("Test error message", extra={"context": "lock"})

    captured = capsys.readouterr()
    stdout_val = captured.out
    stderr_val = captured.err

    # Verify stdout has INFO and SUCCESS
    assert "[INFO]" in stdout_val
    assert "[sync] Test info message" in stdout_val
    assert "[SUCCESS]" in stdout_val
    assert "[alias:docs] Test success message" in stdout_val

    # Verify stderr has WARN and ERROR
    assert "[WARN]" in stderr_val
    assert "[daemon] Test warning message" in stderr_val
    assert "[ERROR]" in stderr_val
    assert "[lock] Test error message" in stderr_val

    # Regex test for format: [<LEVEL>] [<TIMESTAMP>] [<CONTEXT>] <MESSAGE>
    pattern = r"\[(INFO|WARN|ERROR|SUCCESS)\] \[\d{2}:\d{2}:\d{2}\] \[[^\]]+\] .+"
    for line in stdout_val.strip().splitlines():
        assert re.match(pattern, line), f"Line did not match pattern: {line}"
    for line in stderr_val.strip().splitlines():
        assert re.match(pattern, line), f"Line did not match pattern: {line}"


def test_stream_rclone_line(capsys):
    stream_rclone_line("2026/10/02 NOTICE: bisync: Resyncing...")
    captured = capsys.readouterr()
    assert captured.out == "  │ 2026/10/02 NOTICE: bisync: Resyncing...\n"
