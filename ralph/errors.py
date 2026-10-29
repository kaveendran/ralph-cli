class RalphError(Exception):
    """A problem the user can act on.

    `message` says what went wrong; `hint` says what to do about it. The CLI
    prints both and exits with status 1, without a traceback.
    """

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
