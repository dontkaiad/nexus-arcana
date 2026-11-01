import logging

from core.log_setup import quiet_http_loggers


def test_httpx_info_is_suppressed(caplog):
    logging.getLogger("httpx").setLevel(logging.INFO)
    quiet_http_loggers()
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").info("POST https://api.telegram.org/bot123:SECRET/sendMessage")
        logging.getLogger("httpx").warning("real problem")
    text = caplog.text
    assert "SECRET" not in text and "real problem" in text
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


def test_entrypoints_call_quiet_http_loggers():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("arcana/bot.py", "nexus/nexus_bot.py", "zarya/bot.py"):
        assert "quiet_http_loggers()" in (root / rel).read_text(), rel
