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
