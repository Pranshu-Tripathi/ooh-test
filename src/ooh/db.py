from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection

from ooh.config import get_settings


@contextmanager
def get_connection() -> Iterator[Connection]:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as connection:
        yield connection


def check_database() -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("select 1")
            cursor.fetchone()
