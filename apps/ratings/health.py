from django.db import Error, connections


def database_is_ready() -> bool:
    database = connections["default"]
    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Error:
        return False

    return True
