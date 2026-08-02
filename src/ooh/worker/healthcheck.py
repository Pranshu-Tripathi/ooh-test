from ooh.config import get_settings
from ooh.db import check_database, check_schema_current
from ooh.health import check_cache_writable


def main() -> None:
    settings = get_settings()
    check_database()
    check_schema_current()
    check_cache_writable(settings.cache_root)


if __name__ == "__main__":
    main()
