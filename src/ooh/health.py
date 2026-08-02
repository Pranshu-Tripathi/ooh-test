from pathlib import Path
from tempfile import NamedTemporaryFile


def check_cache_writable(cache_root: Path) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(prefix=".ooh-ready-", dir=cache_root):
        pass
