"""Container runtime: the readiness probe and the opt-in log file."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from loguru import logger

from src.settings.logging_config import configure_logging


class ReadinessProbeTests(unittest.IsolatedAsyncioTestCase):
    async def get_ready(self):
        from src.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/ready")

    async def test_ready_when_postgres_answers(self):
        with patch("src.main.postgres_client.fetch_mappings", return_value=[{"ready": 1}]):
            response = await self.get_ready()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"status": "ready"})

    async def test_not_ready_when_postgres_fails(self):
        with patch("src.main.postgres_client.fetch_mappings", side_effect=ConnectionError("db host down")):
            response = await self.get_ready()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["errors"][0]["msg"], "DATABASE_UNAVAILABLE")
        self.assertNotIn("db host down", response.text)


class LoggingConfigurationTests(unittest.TestCase):
    def tearDown(self):
        configure_logging("WARNING", None)

    def test_only_stderr_without_a_path(self):
        # loguru has no public handler listing; its core keeps one entry per sink.
        configure_logging("WARNING", None)
        self.assertEqual(len(logger._core.handlers), 1)
        configure_logging("WARNING", "   ")
        self.assertEqual(len(logger._core.handlers), 1)

    def test_configured_path_receives_records(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "app.log"
            configure_logging("WARNING", str(log_file))
            logger.info("written to the file")
            configure_logging("WARNING", None)  # closes the file sink
            self.assertIn("written to the file", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
