import pytest

from order_intake import Workflow
from order_intake.state import TRANSITIONS, Event, IllegalTransition, State, next_state

ORDER = 'SYNTHETIC_ORDER_ALPHA'
COMPLETE = 'item=SYNTHETIC_TSHIRT;quantity=2;size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA'
INCOMPLETE = 'item=SYNTHETIC_TSHIRT'

ILLEGAL = [(s, e) for s in State for e in Event if e not in TRANSITIONS[s]]
LEGAL = [(s, e, t) for s, moves in TRANSITIONS.items() for e, t in moves.items()]


def test_table_covers_every_state():
    assert set(TRANSITIONS) == set(State)


def test_terminal_states_have_no_exits():
    assert TRANSITIONS[State.APPROVED] == {} and TRANSITIONS[State.REJECTED] == {}


@pytest.mark.parametrize(('state', 'event'), ILLEGAL)
def test_every_illegal_transition_raises(state, event):
    with pytest.raises(IllegalTransition) as caught:
        next_state(state, event)
    assert (caught.value.state, caught.value.event) == (state, event)
    assert isinstance(caught.value, ValueError)  # existing callers catching ValueError still work


@pytest.mark.parametrize(('state', 'event', 'target'), LEGAL)
def test_every_legal_transition(state, event, target):
    assert next_state(state, event) is target


def _flow_in(state):
    """Drive a real Workflow into `state` using only public methods."""
    flow = Workflow()
    if state is State.WAITING_FOR_DETAILS:
        flow.receive(ORDER, INCOMPLETE)
    else:
        flow.receive(ORDER, COMPLETE)
        if state is State.APPROVED:
            flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')
        elif state is State.REJECTED:
            flow.decide(ORDER, 'reject', actor='SYNTHETIC_OWNER')
    assert flow.get(ORDER)[1] is state
    return flow


def _fire(flow, event):
    if event is Event.SUBMIT_COMPLETE:
        flow.receive(ORDER, 'quantity=3')
    elif event is Event.SUBMIT_INCOMPLETE:
        flow.receive(ORDER, 'quantity=0')
    else:
        flow.decide(ORDER, event.value, actor='SYNTHETIC_OWNER')


@pytest.mark.parametrize(('state', 'event'), [(s, e) for s, e in ILLEGAL if s is not State.NEW])
def test_illegal_transitions_through_workflow_do_not_mutate(state, event):
    flow = _flow_in(state)
    try:
        before = flow.get(ORDER)
        with pytest.raises(IllegalTransition):
            _fire(flow, event)
        assert flow.get(ORDER) == before
    finally:
        flow.close()
