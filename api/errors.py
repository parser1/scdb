"""HTTP error shape used by every route."""

from __future__ import annotations


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, operation_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.operation_id = operation_id

    def body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "operation_id": self.operation_id,
            }
        }


class InvalidTransition(Exception):
    pass


class Pause(Exception):
    """Leave the operation running so the next tick retries the current step."""


class WorkflowError(Exception):
    def __init__(self, message: str, cluster_status: str | None = "error"):
        super().__init__(message)
        self.cluster_status = cluster_status
