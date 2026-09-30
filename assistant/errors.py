"""How a step ends early: Halt stops the task with a status and a message for the user; Retry does nothing this
step and hands its message to the planner as feedback."""


class Halt(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Retry(Exception):
    """Nothing was done this step; the message is feedback for the planner's next turn."""
