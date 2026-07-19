from unittest.mock import MagicMock

from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase

from ..health import database_is_ready


class DatabaseReadinessTests(SimpleTestCase):
    def test_executes_query_against_database(self):
        database = MagicMock()
        cursor = database.cursor.return_value.__enter__.return_value

        self.assertTrue(database_is_ready(database=database))

        cursor.execute.assert_called_once_with("SELECT 1")

    def test_reports_cursor_errors_as_not_ready(self):
        database = MagicMock()
        database.cursor.side_effect = InterfaceError("connection unavailable")

        self.assertFalse(database_is_ready(database=database))

    def test_reports_query_errors_as_not_ready(self):
        database = MagicMock()
        cursor = database.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = OperationalError("database unavailable")

        self.assertFalse(database_is_ready(database=database))

    def test_does_not_hide_unexpected_errors(self):
        database = MagicMock()
        database.cursor.side_effect = RuntimeError("programming error")

        with self.assertRaisesMessage(RuntimeError, "programming error"):
            database_is_ready(database=database)
