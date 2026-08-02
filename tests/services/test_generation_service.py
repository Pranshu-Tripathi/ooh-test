from types import SimpleNamespace
from uuid import uuid4

from ooh.db.models import ContextPackType
from ooh.services.test_generation import TestGenerationService as GenerationService


def test_enqueue_generation_job_uses_configured_default_per_category() -> None:
    repository_id = uuid4()
    snapshot_id = uuid4()
    job_repo = CapturingJobRepo()
    service = GenerationService(
        repository_service=SimpleNamespace(),
        repository_repo=SimpleNamespace(
            get=lambda requested_id: SimpleNamespace(id=requested_id)
        ),
        repo_snapshot_repo=SimpleNamespace(
            latest_for_repository=lambda _repository_id: SimpleNamespace(id=snapshot_id)
        ),
        context_pack_repo=SimpleNamespace(),
        drift_event_repo=SimpleNamespace(),
        generated_test_repo=SimpleNamespace(),
        job_repo=job_repo,
        questions_per_category_default=3,
    )

    service.enqueue_generate_test_job(
        repository_id,
        pack_types=[
            ContextPackType.LOW_LEVEL_COMPONENTS,
            ContextPackType.DESIGN_DECISIONS,
        ],
    )

    assert job_repo.payload == {
        "repository_id": str(repository_id),
        "snapshot_id": str(snapshot_id),
        "generation_plan": [
            {
                "category": "low_level_components",
                "question_count": 3,
            },
            {
                "category": "design_decisions",
                "question_count": 3,
            },
        ],
    }


class CapturingJobRepo:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def enqueue(self, **kwargs: object) -> object:
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        self.payload = payload
        return SimpleNamespace(payload=payload)
