from uuid import UUID

from ooh.db.models import SavedLearningRead
from ooh.db.repos import RepositoryRepo, SavedLearningRepo
from ooh.services.exceptions import NotFoundError


class LearningService:
    def __init__(
        self,
        *,
        repository_repo: RepositoryRepo,
        saved_learning_repo: SavedLearningRepo,
    ) -> None:
        self.repository_repo = repository_repo
        self.saved_learning_repo = saved_learning_repo

    def list_repository_learnings(self, repository_id: UUID) -> list[SavedLearningRead]:
        if self.repository_repo.get(repository_id) is None:
            raise NotFoundError("repository not found")
        return self.saved_learning_repo.list_for_repository(repository_id)
