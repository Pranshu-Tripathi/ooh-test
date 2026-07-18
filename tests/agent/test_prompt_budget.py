from ooh.agent.prompt_budget import (
    build_prompt_context_view,
    compact_tool_observation,
    json_size_bytes,
    serialize_prompt_context,
)


def test_prompt_context_view_is_structured_and_bounded() -> None:
    context_pack = {
        "schema_version": 1,
        "pack_type": "low_level_components",
        "repository": {
            "name": "repo",
            "source_type": "local_path",
            "source_uri": "/private/host/path",
        },
        "snapshot": {
            "commit_sha": "abc123",
            "index_uri": "/private/cache/index.json",
        },
        "source_refs": [
            {"source_type": "code", "source_uri": f"code:src/file_{index}.py"}
            for index in range(80)
        ],
        "included_files": [
            {
                "path": f"src/file_{index}.py",
                "language": "python",
                "content_excerpt": {"text": "def run():\n    return 1\n" * 500},
            }
            for index in range(20)
        ],
    }

    view = build_prompt_context_view(context_pack, max_bytes=3_000)
    serialized = serialize_prompt_context(view)

    assert len(serialized.encode("utf-8")) <= 3_000
    assert view.used_bytes == len(serialized.encode("utf-8"))
    assert view.original_bytes > view.used_bytes
    assert view.truncated is True
    assert view.payload["source_refs"][0]["source_uri"] == "code:src/file_0.py"
    assert "/private/host/path" not in serialized
    assert "/private/cache/index.json" not in serialized


def test_tool_observation_is_bounded_without_losing_call_identity() -> None:
    observation = {
        "schema_version": 1,
        "planned_call_count": 2,
        "completed_call_count": 2,
        "failed_call_count": 0,
        "tool_calls": [
            {
                "call_id": f"read-{index}",
                "tool_name": "repo.read_file_range",
                "arguments": {"path": f"src/file_{index}.py"},
                "payload": {"content": "return value\n" * 1_000},
                "evidence_refs": [
                    {"source_type": "code", "source_uri": f"code:src/file_{index}.py"}
                ],
            }
            for index in range(2)
        ],
        "failed_tool_calls": [],
    }

    compact = compact_tool_observation(observation, max_bytes=1_200)

    assert json_size_bytes(compact) <= 1_200
    assert compact["tool_calls"][0]["call_id"] == "read-0"
    assert compact["tool_calls"][0]["tool_name"] == "repo.read_file_range"
    assert compact["truncated"] is True
