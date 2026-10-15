"""Business services package. All business rules live here."""


class AppError(Exception):
    """Service-level error with an API code, message and HTTP status.

    Raised by services; converted to the ErrorOut JSON shape by the
    handler in app/main.py. Keeps API/dashboard routes thin.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
