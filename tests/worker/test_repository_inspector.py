import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ooh.db.models import RepositoryRead, RepositorySourceType, RepositoryStatus
from ooh.worker.repository_inspector import LocalRepositoryInspector


def test_repository_inspector_writes_tree_sitter_structural_index(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_git_head(repository_path, "abc1234567890abc1234567890abc1234567890abc")
    write_file(
        repository_path / "src" / "app.py",
        "\n".join(
            [
                "import os",
                "from pathlib import Path, PurePath as PP",
                "",
                "class Service:",
                "    def handle(self, value: str) -> str:",
                "        return value.upper()",
                "",
                "def helper():",
                "    return Service()",
                "",
            ]
        ),
    )
    write_file(repository_path / "README.md", "Repository guidance.\n")

    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")

    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)

    manifest = json.loads(Path(snapshot.index_uri).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["index"]["kind"] == "tree_sitter_structural_index"
    assert manifest["index"]["supported_languages"] == ["python"]
    assert manifest["index"]["indexed_file_count"] == 1
    assert manifest["index"]["symbol_count"] == 3
    assert manifest["index"]["import_count"] == 2

    files_payload = read_artifact(manifest, "files")
    symbols_payload = read_artifact(manifest, "symbols")
    imports_payload = read_artifact(manifest, "imports")

    app_file = next(file for file in files_payload["files"] if file["path"] == "src/app.py")
    readme_file = next(file for file in files_payload["files"] if file["path"] == "README.md")
    assert app_file["language"] == "python"
    assert app_file["parse_status"] == "parsed"
    assert app_file["line_count"] == 9
    assert readme_file["language"] is None
    assert readme_file["parse_status"] == "unsupported_language"

    symbols_by_name = {symbol["qualified_name"]: symbol for symbol in symbols_payload["symbols"]}
    assert symbols_by_name["Service"]["kind"] == "class"
    assert symbols_by_name["Service"]["start_line"] == 4
    assert symbols_by_name["Service.handle"]["kind"] == "method"
    assert symbols_by_name["Service.handle"]["parent_qualified_name"] == "Service"
    assert symbols_by_name["Service.handle"]["signature"] == "def handle(self, value: str) -> str"
    assert symbols_by_name["helper"]["kind"] == "function"

    imports = imports_payload["imports"]
    assert imports[0]["kind"] == "import"
    assert imports[0]["names"] == [{"name": "os", "alias": None}]
    assert imports[1]["kind"] == "from_import"
    assert imports[1]["module"] == "pathlib"
    assert imports[1]["names"] == [
        {"name": "Path", "alias": None},
        {"name": "PurePath", "alias": "PP"},
    ]


def read_artifact(manifest: dict[str, object], name: str) -> dict[str, object]:
    index = manifest["index"]
    assert isinstance(index, dict)
    artifacts = index["artifacts"]
    assert isinstance(artifacts, dict)
    artifact = artifacts[name]
    assert isinstance(artifact, dict)
    uri = artifact["uri"]
    assert isinstance(uri, str)
    return json.loads(Path(uri).read_text(encoding="utf-8"))


def build_repository(repository_path: Path) -> RepositoryRead:
    now = datetime.now(UTC)
    return RepositoryRead(
        id=uuid4(),
        name="repo",
        source_type=RepositorySourceType.LOCAL_PATH,
        source_uri=str(repository_path),
        default_branch=None,
        token_ref=None,
        status=RepositoryStatus.PENDING,
        last_processed_commit_sha=None,
        last_indexed_at=None,
        created_at=now,
        updated_at=now,
    )


def write_git_head(repository_path: Path, commit_sha: str) -> None:
    git_dir = repository_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(commit_sha, encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
