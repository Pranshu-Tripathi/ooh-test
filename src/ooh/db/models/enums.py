from enum import StrEnum


class RepositorySourceType(StrEnum):
    LOCAL_PATH = "local_path"
    GITHUB = "github"


class RepositoryStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"


class RepoSnapshotStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class JobType(StrEnum):
    INGEST_REPOSITORY = "ingest_repository"
    COMPUTE_DRIFT = "compute_drift"
    GENERATE_TEST = "generate_test"
    JUDGE_ANSWER = "judge_answer"
    REFRESH_GITHUB_METADATA = "refresh_github_metadata"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DriftSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentRunType(StrEnum):
    DRIFT_ANALYSIS = "drift_analysis_run"
    TEST_GENERATION = "test_generation_run"
    ANSWER_JUDGING = "answer_judging_run"
    GUIDANCE_REFRESH = "guidance_refresh_run"


class AgentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_ON_MODEL = "waiting_on_model"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"


class AgentStepType(StrEnum):
    LOAD_REPOSITORY_STATE = "load_repository_state"
    BUILD_TEST_PLAN = "build_test_plan"
    BUILD_CONTEXT_PACK = "build_context_pack"
    GENERATE_QUESTIONS = "generate_questions"
    VALIDATE_OUTPUT = "validate_output"
    VERIFY_EVIDENCE = "verify_evidence"
    DEDUPE_AND_BALANCE = "dedupe_and_balance"
    PERSIST_RESULT = "persist_result"
    BUILD_GRADING_CONTEXT = "build_grading_context"
    JUDGE_ANSWER = "judge_answer"
    SUGGEST_LEARNING = "suggest_learning"


class AgentArtifactType(StrEnum):
    INPUT = "input"
    CONTEXT_PACK = "context_pack"
    PROMPT = "prompt"
    RAW_MODEL_RESPONSE = "raw_model_response"
    VALIDATED_OUTPUT = "validated_output"
    TRACE = "trace"


class ProvenanceRefType(StrEnum):
    CODE = "code"
    GUIDANCE = "guidance"
    PROMPT = "prompt"
    MODEL = "model"
    SNAPSHOT = "snapshot"
    CONTEXT_PACK = "context_pack"


class ContextPackType(StrEnum):
    HIGH_LEVEL_DESIGN = "high_level_design"
    LOW_LEVEL_COMPONENTS = "low_level_components"
    DESIGN_DECISIONS = "design_decisions"
    FUTURE_IMPROVEMENTS = "future_improvements"
    ACTIVE_PR = "active_pr"


class ContextPackSourceType(StrEnum):
    CODE = "code"
    GUIDANCE = "guidance"
    DRIFT = "drift"
    ATTENTION_PROFILE = "attention_profile"
    HISTORY = "history"


class GuidanceSourceType(StrEnum):
    AGENTS = "agents"
    CLAUDE = "claude"
    CURSOR = "cursor"
    DOCS = "docs"
    ADR = "adr"
    DESIGN = "design"
    README = "readme"
    USER_SKILL = "user_skill"
    CUSTOM = "custom"


class GeneratedTestCategory(StrEnum):
    HIGH_LEVEL_DESIGN = "high_level_design"
    LOW_LEVEL_COMPONENTS = "low_level_components"
    DESIGN_DECISIONS = "design_decisions"
    FUTURE_IMPROVEMENTS = "future_improvements"
    ACTIVE_PR = "active_pr"


class TestResultStatus(StrEnum):
    PASSING = "passing"
    NEEDS_REVIEW = "needs_review"
    GETTING_OUT_OF_HAND = "getting_out_of_hand"
