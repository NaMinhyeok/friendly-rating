from django.db import Error, connections
from django.db.backends.base.base import BaseDatabaseWrapper


def database_is_ready(*, database: BaseDatabaseWrapper | None = None) -> bool:
    if database is None:
        database = connections["default"]

    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Error:
        return False

    return True
