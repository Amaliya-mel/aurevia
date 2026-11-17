import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import server


class LeadStorageTests(unittest.TestCase):
    def test_save_lead_appends_complete_json_record(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            leads_file = data_dir / "leads.jsonl"
            payload = {
                "name": "Анна",
                "contact": "anna@example.com",
                "location": "Москва",
                "objectType": "Квартира",
                "message": "Нужен дизайн-проект",
            }
            with (
                mock.patch.object(server, "DATA_DIR", data_dir),
                mock.patch.object(server, "LEADS_FILE", leads_file),
            ):
                saved = server.save_lead(
                    payload,
                    received_at=datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc),
                )

            stored = json.loads(leads_file.read_text(encoding="utf-8"))
            self.assertEqual(stored, saved)
            self.assertEqual(stored["contact"], "anna@example.com")
            self.assertIn("id", stored)

    def test_load_today_leads_uses_moscow_day_and_skips_invalid_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            rows = [
                {
                    "id": "today",
                    "received_at": "2026-07-12T21:30:00+00:00",
                    "name": "Анна",
                },
                {
                    "id": "yesterday",
                    "received_at": "2026-07-12T20:30:00+00:00",
                    "name": "Иван",
                },
            ]
            path.write_text(
                "\n".join(
                    [json.dumps(row, ensure_ascii=False) for row in rows] + ["not-json"]
                ),
                encoding="utf-8",
            )

            leads = server.load_today_leads(
                now=datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc),
                path=path,
                timezone_name="Europe/Moscow",
            )

            self.assertEqual([lead["id"] for lead in leads], ["today"])

    def test_load_today_leads_returns_empty_for_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            leads = server.load_today_leads(path=Path(directory) / "missing.jsonl")
            self.assertEqual(leads, [])

    def test_prompt_contains_all_personal_fields_and_constraints(self):
        prompt = server.build_summary_prompt(
            [
                {
                    "name": "Анна",
                    "contact": "+7 900 000-00-00",
                    "location": "Москва",
                    "objectType": "Квартира",
                    "message": "Нужен дизайн-проект",
                }
            ]
        )
        self.assertIn("Анна", prompt)
        self.assertIn("+7 900 000-00-00", prompt)
        self.assertIn("Не придумывай факты", prompt)

    @mock.patch("server.subprocess.run")
    def test_codex_summary_uses_requested_model_and_sandbox(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="Краткая сводка\n")

        result = server.run_codex_summary([{"name": "Анна"}])

        command = run.call_args.args[0]
        self.assertEqual(command[0], server.CODEX_BIN)
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn("read-only", command)
        self.assertEqual(result, "Краткая сводка")


if __name__ == "__main__":
    unittest.main()
