from ooh.db import check_database, check_schema_current


def main() -> None:
    check_database()
    check_schema_current()


if __name__ == "__main__":
    main()
