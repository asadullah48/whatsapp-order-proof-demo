import datetime
import json

import pytest

from order_intake import Workflow
from order_intake.state import IllegalTransition, State

ORDER = 'SYNTHETIC_ORDER_ALPHA'
OWNER = 'SYNTHETIC_OWNER'


def read_log(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_log_matches_state_history(tmp_path):
    log = tmp_path / 'audit.jsonl'
    flow = Workflow(audit_path=log)
    steps = [  # (actor, action)
        ('SYNTHETIC_CUSTOMER', lambda: flow.receive(ORDER, 'item=SYNTHETIC_TSHIRT;quantity=2')),
        ('SYNTHETIC_CUSTOMER', lambda: flow.receive(ORDER, 'size=M')),  # still incomplete: no change
        ('SYNTHETIC_CUSTOMER', lambda: flow.receive(ORDER, 'address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA')),
        (OWNER, lambda: flow.decide(ORDER, 'correct', actor=OWNER)),
        ('SYNTHETIC_CUSTOMER', lambda: flow.receive(ORDER, 'quantity=3')),
        (OWNER, lambda: flow.decide(ORDER, 'approve', actor=OWNER)),
    ]
    history, expected = [State.NEW], []
    try:
        for actor, action in steps:
            action()
            now = flow.get(ORDER)[1]
            if now != history[-1]:
                expected.append({'order': ORDER, 'from': history[-1].value, 'to': now.value, 'actor': actor})
            history.append(now)
        with pytest.raises(IllegalTransition):  # rejected moves leave no trace
            flow.decide(ORDER, 'reject', actor=OWNER)
    finally:
        flow.close()

    lines = read_log(log)
    assert [{k: v for k, v in line.items() if k != 'time'} for line in lines] == expected
    assert len(expected) == 5
    for line in lines:
        assert set(line) == {'time', 'order', 'from', 'to', 'actor'}
        assert datetime.datetime.fromisoformat(line['time']).tzinfo is not None


def test_log_is_append_only_across_instances(tmp_path):
    log = tmp_path / 'audit.jsonl'
    db = str(tmp_path / 'orders.sqlite')
    first = Workflow(db, log)
    first.receive(ORDER, 'quantity=2')
    first.close()
    before = log.read_text(encoding='utf-8')
    second = Workflow(db, log)
    second.receive(ORDER, 'item=SYNTHETIC_TSHIRT;size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA')
    second.close()
    after = log.read_text(encoding='utf-8')
    assert after.startswith(before)
    assert [line['to'] for line in read_log(log)] == ['waiting_for_details', 'waiting_for_approval']


def test_failed_audit_write_rolls_back_state(tmp_path):
    flow = Workflow(audit_path=tmp_path)  # a directory: open(..., 'a') fails
    try:
        with pytest.raises(OSError):
            flow.receive(ORDER, 'quantity=2')
        with pytest.raises(ValueError, match='Unknown order'):
            flow.get(ORDER)
    finally:
        flow.close()

