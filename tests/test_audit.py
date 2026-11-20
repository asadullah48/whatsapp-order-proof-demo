import datetime
import json
import sqlite3

import pytest

from order_intake import Workflow
from order_intake.state import IllegalTransition, State

ORDER = 'SYNTHETIC_ORDER_ALPHA'
OWNER = 'SYNTHETIC_OWNER'
COMPLETE = 'item=SYNTHETIC_TSHIRT;quantity=2;size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA'


def export_lines(flow):
    return [json.loads(line) for line in flow.export_audit_jsonl().splitlines()]


def committed(path):
    """Read what is actually on disk, through a connection the workflow never touched."""
    with sqlite3.connect(path) as db:
        orders = db.execute('SELECT id, status FROM orders').fetchall()
        audit = db.execute('SELECT order_id, from_state, to_state FROM audit ORDER BY seq').fetchall()
    return orders, audit


def test_export_matches_state_history():
    flow = Workflow()
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
        lines = export_lines(flow)
    finally:
        flow.close()

    assert [{k: v for k, v in line.items() if k != 'time'} for line in lines] == expected
    assert len(expected) == 5
    for line in lines:
        assert set(line) == {'time', 'order', 'from', 'to', 'actor'}
        assert datetime.datetime.fromisoformat(line['time']).tzinfo is not None


def test_audit_rows_persist_across_instances(tmp_path):
    db = str(tmp_path / 'orders.sqlite')
    first = Workflow(db)
    first.receive(ORDER, 'quantity=2')
    first.close()
    second = Workflow(db)
    second.receive(ORDER, 'item=SYNTHETIC_TSHIRT;size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA')
    second.close()
    assert committed(db)[1] == [(ORDER, 'new', 'waiting_for_details'),
                                (ORDER, 'waiting_for_details', 'waiting_for_approval')]


@pytest.mark.parametrize('operation', ['receive', 'decide'])
def test_failure_after_audit_write_persists_neither(tmp_path, monkeypatch, operation):
    db = str(tmp_path / 'orders.sqlite')
    flow = Workflow(db)
    if operation == 'decide':
        flow.receive(ORDER, COMPLETE)  # committed setup: one order, one audit row
    before = committed(db)

    real_audit = Workflow._audit
    written = []

    def audit_then_crash(self, *args):
        real_audit(self, *args)  # the audit INSERT really runs...
        written.append(self.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0])
        raise RuntimeError('simulated crash after audit write')  # ...then the step fails

    monkeypatch.setattr(Workflow, '_audit', audit_then_crash)
    with pytest.raises(RuntimeError, match='simulated crash'):
        if operation == 'receive':
            flow.receive(ORDER, COMPLETE)
        else:
            flow.decide(ORDER, 'approve', actor=OWNER)
    flow.close()

    assert written == [len(before[1]) + 1]  # the row existed inside the transaction
    assert committed(db) == before           # but neither it nor the state change was committed


def test_audit_table_is_append_only():
    flow = Workflow()
    try:
        flow.receive(ORDER, 'quantity=2')
        for sql in ("UPDATE audit SET actor='SYNTHETIC_OWNER'", 'DELETE FROM audit'):
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                flow.db.execute(sql)
        assert len(export_lines(flow)) == 1
    finally:
        flow.close()


def test_demo_writes_audit_export(tmp_path, capsys):
    from order_intake import run_demo
    out = tmp_path / 'audit.jsonl'
    run_demo(audit_path=out)
    assert 'Local outbox records: 1' in capsys.readouterr().out
    assert [json.loads(line)['to'] for line in out.read_text(encoding='utf-8').splitlines()] == [
        'waiting_for_details', 'waiting_for_approval', 'waiting_for_details', 'waiting_for_approval', 'approved']
