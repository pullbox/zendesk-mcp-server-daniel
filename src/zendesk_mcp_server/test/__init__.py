import unittest


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "zendesk_mcp_server.test.ticket_test"
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
