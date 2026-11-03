"""ApiError: raise it in a route to answer {"error": "<message>"} with a status (400 by default)."""


class ApiError(Exception):
    """A friendly error from a route: the message is shown to the user as it is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status
