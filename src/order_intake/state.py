"""Order state machine: one explicit table decides every legal move."""
from enum import StrEnum


class State(StrEnum):
    NEW = 'new'  # before the first message; never stored
    WAITING_FOR_DETAILS = 'waiting_for_details'
    WAITING_FOR_APPROVAL = 'waiting_for_approval'
    APPROVED = 'approved'
    REJECTED = 'rejected'


class Event(StrEnum):
    SUBMIT_INCOMPLETE = 'submit_incomplete'
    SUBMIT_COMPLETE = 'submit_complete'
    APPROVE = 'approve'
    REJECT = 'reject'
    CORRECT = 'correct'


_SUBMIT = {Event.SUBMIT_INCOMPLETE: State.WAITING_FOR_DETAILS,
           Event.SUBMIT_COMPLETE: State.WAITING_FOR_APPROVAL}

TRANSITIONS: dict[State, dict[Event, State]] = {
    State.NEW: dict(_SUBMIT),
    State.WAITING_FOR_DETAILS: dict(_SUBMIT),
    State.WAITING_FOR_APPROVAL: {**_SUBMIT,
                                 Event.APPROVE: State.APPROVED,
                                 Event.REJECT: State.REJECTED,
                                 Event.CORRECT: State.WAITING_FOR_DETAILS},
    State.APPROVED: {},
    State.REJECTED: {},
}


class IllegalTransition(ValueError):
    """Raised when an event is not allowed from the current state."""

    def __init__(self, state, event):
        super().__init__(f'Illegal transition: {event} from {state}')
        self.state, self.event = state, event


def next_state(state, event):
    try:
        return TRANSITIONS[State(state)][Event(event)]
    except KeyError:
        raise IllegalTransition(state, event) from None
