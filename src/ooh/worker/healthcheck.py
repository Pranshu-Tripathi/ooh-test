from pathlib import Path

from ooh.config import get_settings
from ooh.db import check_database, check_schema_current


def main() -> None:
    settings = get_settings()
    check_database()
    check_schema_current()
    Path(settings.cache_root).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
