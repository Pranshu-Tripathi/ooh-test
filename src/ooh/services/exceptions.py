class ServiceError(Exception):
    """Base class for expected application service failures."""


class NotFoundError(ServiceError):
    """Raised when a requested domain record does not exist."""


class ConflictError(ServiceError):
    """Raised when a request is valid but the current domain state cannot satisfy it."""
