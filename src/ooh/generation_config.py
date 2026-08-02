DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY = 3
MAX_GENERATION_QUESTIONS_PER_CATEGORY = 5
MAX_GENERATION_QUESTIONS_PER_JOB = 15
MAX_GENERATION_CATEGORY_COUNT = 5
MAX_DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY = min(
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB // MAX_GENERATION_CATEGORY_COUNT,
)


def validate_default_generation_questions_per_category(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("default generation questions per category must be an integer")
    if value < 1 or value > MAX_DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY:
        raise ValueError(
            "default generation questions per category must be between 1 and "
            f"{MAX_DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY}"
        )
    return value
