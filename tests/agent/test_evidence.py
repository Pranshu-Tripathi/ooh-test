from ooh.agent.evidence import collect_available_evidence_refs, verify_generated_test_evidence


def test_collect_available_evidence_refs_from_context_pack_sources() -> None:
    refs = collect_available_evidence_refs(
        {
            "source_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/app.py",
                    "content_hash": "code-hash",
                }
            ],
            "included_guidance": [
                {
                    "path": "AGENTS.md",
                    "content_hash": "guidance-hash",
                }
            ],
            "drift": {"id": "drift-1"},
        }
    )

    assert refs["code:src/app.py"]["content_hash"] == "code-hash"
    assert refs["guidance:AGENTS.md"]["source_type"] == "guidance"
    assert refs["drift_event:drift-1"]["source_type"] == "drift"


def test_verify_generated_test_evidence_enriches_refs() -> None:
    result = verify_generated_test_evidence(
        {
            "type": "short_answer",
            "question": "What changed?",
            "expected_answer": "The app changed.",
            "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
        },
        {
            "source_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/app.py",
                    "content_hash": "code-hash",
                }
            ]
        },
    )

    assert result.is_valid
    assert result.payload["evidence_refs"] == [
        {
            "source_type": "code",
            "source_uri": "code:src/app.py",
            "content_hash": "code-hash",
        }
    ]


def test_verify_generated_test_evidence_rejects_missing_refs_when_sources_exist() -> None:
    result = verify_generated_test_evidence(
        {
            "type": "short_answer",
            "question": "What changed?",
            "expected_answer": "The app changed.",
            "evidence_refs": [],
        },
        {"source_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}]},
    )

    assert not result.is_valid
    assert result.rejected_refs[0].code == "missing_evidence_refs"


def test_verify_generated_test_evidence_rejects_unknown_refs() -> None:
    result = verify_generated_test_evidence(
        {
            "type": "short_answer",
            "question": "What changed?",
            "expected_answer": "The app changed.",
            "evidence_refs": [{"source_type": "code", "source_uri": "code:missing.py"}],
        },
        {"source_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}]},
    )

    assert not result.is_valid
    assert result.rejected_refs[0].code == "unknown_source_uri"


def test_verify_generated_test_evidence_rejects_content_hash_mismatch() -> None:
    result = verify_generated_test_evidence(
        {
            "type": "short_answer",
            "question": "What changed?",
            "expected_answer": "The app changed.",
            "evidence_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/app.py",
                    "content_hash": "wrong-hash",
                }
            ],
        },
        {
            "source_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/app.py",
                    "content_hash": "code-hash",
                }
            ]
        },
    )

    assert not result.is_valid
    assert result.rejected_refs[0].code == "content_hash_mismatch"
