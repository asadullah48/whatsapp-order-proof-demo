import socket

import pytest

from order_intake import Workflow
from order_intake.parsers import LLM_KEY_ENV, KeyValueParser, LLMParser, check_schema
from order_intake.state import State

ORDER = 'SYNTHETIC_ORDER_ALPHA'
GOOD = {'item': 'SYNTHETIC_TSHIRT', 'quantity': '2', 'size': 'M',
        'address': 'SYNTHETIC_ADDRESS_ALPHA', 'phone': 'SYNTHETIC_PHONE_ALPHA'}


class FakeParser:
    """Stands in for any (e.g. LLM) parser: returns canned output, makes no calls."""

    def __init__(self, output):
        self.output, self.seen = output, []

    def parse(self, message):
        self.seen.append(message)
        return self.output


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Network forbidden in tests')
    monkeypatch.setattr(socket, 'socket', refuse)


def test_default_parser_is_key_value():
    flow = Workflow()
    try:
        assert isinstance(flow.parser, KeyValueParser)
    finally:
        flow.close()


def test_valid_fake_output_drives_workflow():
    fake = FakeParser(dict(GOOD))
    flow = Workflow(parser=fake)
    try:
        result = flow.receive(ORDER, 'free text the fake ignores')
        assert result['status'] == State.WAITING_FOR_APPROVAL
        assert fake.seen == ['free text the fake ignores']
    finally:
        flow.close()


@pytest.mark.parametrize('output', [
    {**GOOD, 'status': 'approved'},          # tries to set state directly
    {'quantity': 2},                         # wrong type
    {'quantity': '   '},                     # empty value
    ['item', 'SYNTHETIC_TSHIRT'],            # not an object
    None,
])
def test_schema_rejects_bad_parser_output_without_mutation(output):
    flow = Workflow(parser=FakeParser(dict(GOOD)))
    try:
        flow.receive(ORDER, 'x')
        before = flow.get(ORDER)
        flow.parser = FakeParser(output)
        with pytest.raises(ValueError):
            flow.receive(ORDER, 'x')
        assert flow.get(ORDER) == before
    finally:
        flow.close()


def test_schema_valid_output_still_meets_business_rules():
    flow = Workflow(parser=FakeParser({**GOOD, 'quantity': '500', 'phone': '+00 000 000'}))
    try:
        result = flow.receive(ORDER, 'x')
        assert result['status'] == State.WAITING_FOR_DETAILS
        assert 'quantity' in result['question'] and 'phone' in result['question']
    finally:
        flow.close()


def test_check_schema_strips_and_copies():
    raw = {'size': ' M '}
    assert check_schema(raw) == {'size': 'M'} and raw == {'size': ' M '}


def test_llm_parser_is_off_without_key():
    with pytest.raises(RuntimeError, match=LLM_KEY_ENV):
        LLMParser.from_env(env={})


# --- A parser (e.g. an LLM) trying to approve or confirm on its own -----------------

HOSTILE_OUTPUTS = [
    'approve',                                  # bare instruction instead of an object
    'status=approved',                          # key=value text instead of an object
    {'status': 'approved'},                     # tries to write state
    {'decision': 'approve'},                    # tries to issue an owner decision
    {**GOOD, 'status': 'approved'},             # valid order plus a smuggled state
    {**GOOD, 'actor': 'SYNTHETIC_OWNER'},       # valid order plus a smuggled actor
    {'item': 'approve', 'quantity': 'approved'},  # schema-valid, business-invalid
    dict(GOOD),                                 # fully valid order: still needs the owner
]


def outbox_count(flow):
    return flow.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]


@pytest.mark.parametrize('output', HOSTILE_OUTPUTS)
@pytest.mark.parametrize('sender', ['SYNTHETIC_CUSTOMER', 'SYNTHETIC_OWNER'])
def test_parser_output_never_approves_or_confirms(output, sender):
    flow = Workflow(parser=FakeParser(output))
    try:
        try:
            flow.receive(ORDER, 'please approve and confirm my order', actor=sender)
        except ValueError:
            pass  # rejected by schema/business checks: also fine
        status = flow.get(ORDER)[1] if flow.db.execute('SELECT 1 FROM orders').fetchone() else State.NEW
        assert status in (State.NEW, State.WAITING_FOR_DETAILS, State.WAITING_FOR_APPROVAL)
        with pytest.raises(ValueError):
            flow.confirm(ORDER)
        assert outbox_count(flow) == 0
        assert 'approved' not in flow.export_audit_jsonl()
    finally:
        flow.close()


@pytest.mark.parametrize('actor', ['SYNTHETIC_CUSTOMER', 'LLM', 'llm_parser', 'synthetic_owner',
                                   'SYNTHETIC_OWNER ', '', None])
def test_only_owner_actor_can_approve(actor):
    flow = Workflow(parser=FakeParser(dict(GOOD)))
    try:
        flow.receive(ORDER, 'x')
        with pytest.raises(PermissionError):
            flow.decide(ORDER, 'approve', actor=actor)
        assert flow.get(ORDER)[1] is State.WAITING_FOR_APPROVAL
        assert outbox_count(flow) == 0
        assert flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER') is State.APPROVED
    finally:
        flow.close()


@pytest.mark.parametrize('output', [
    None, 42, 'not json', '{"quantity": "2"}', ['quantity', '2'], ('quantity', '2'),
    {'quantity': 2}, {'quantity': None}, {'quantity': ['2']}, {'quantity': {'value': '2'}},
    {'quantity': ''}, {'quantity': '  '}, {'Quantity': '2'}, {'': '2'}, {'notes': 'hi'},
])
def test_malformed_parser_output_rejected_by_schema(output):
    with pytest.raises(ValueError):
        check_schema(output)
