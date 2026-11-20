"""Offline SYNTHETIC order workflow. Python standard library only."""
import csv
import datetime
import io
import json
import sqlite3
from pathlib import Path

from .parsers import FIELDS, KeyValueParser, check_schema
from .state import Event, State, next_state

CATALOGUE = {'SYNTHETIC_TSHIRT': ('S', 'M', 'L')}
EDGES = (
    ('Input', 'parse_fields'), ('parse_fields', 'validate'),
    ('validate', 'receive'), ('receive', 'SQLite'),
    ('SQLite', 'decide'), ('decide', 'SQLite'),
    ('SQLite', 'confirm'), ('confirm', 'Local_outbox'),
    ('SQLite', 'export_approved_csv'),
)

def diagram():
    return 'flowchart LR\n' + '\n'.join(f'    {a} --> {b}' for a, b in EDGES)

def validate(fields):
    issues = []
    for name in FIELDS:
        if not fields.get(name):
            issues.append(name)
    if 'item' in fields and fields['item'] not in CATALOGUE:
        issues.append('item: use SYNTHETIC_TSHIRT')
    if 'quantity' in fields:
        value = fields['quantity']
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 100:
            issues.append('quantity: integer 1..100 required')
    if 'size' in fields and fields['size'] not in ('S', 'M', 'L'):
        issues.append('size: S/M/L required')
    if 'address' in fields and not fields['address'].startswith('SYNTHETIC_ADDRESS_'):
        issues.append('address: synthetic placeholder required')
    if 'phone' in fields and not fields['phone'].startswith('SYNTHETIC_PHONE_'):
        issues.append('phone: non-dialable synthetic placeholder required')
    return issues

class Workflow:
    def __init__(self, database=':memory:', parser=None):
        self.db = sqlite3.connect(database)
        self.parser = parser or KeyValueParser()
        self.db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, fields TEXT NOT NULL, status TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (order_id TEXT PRIMARY KEY REFERENCES orders(id), message TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, '
                        'order_id TEXT NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL, actor TEXT NOT NULL)')
        # Append-only inside SQLite; someone with file access can still drop these triggers.
        for verb in ('UPDATE', 'DELETE'):
            self.db.execute(f'CREATE TRIGGER IF NOT EXISTS audit_no_{verb.lower()} BEFORE {verb} ON audit '
                            "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, order_id):
        row = self.db.execute('SELECT fields,status FROM orders WHERE id=?', (order_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown order')
        return json.loads(row[0]), State(row[1])

    def _audit(self, order_id, old, new, actor):
        # Same connection and open transaction as the state write: both commit or neither does.
        if old == new:
            return
        self.db.execute('INSERT INTO audit (time, order_id, from_state, to_state, actor) VALUES (?,?,?,?,?)',
                        (datetime.datetime.now(datetime.UTC).isoformat(), order_id, old.value, new.value, actor))

    def export_audit_jsonl(self):
        """One JSON line per audit row, oldest first. An export of the table, not the record itself."""
        rows = self.db.execute('SELECT time, order_id, from_state, to_state, actor FROM audit ORDER BY seq')
        return ''.join(json.dumps({'time': t, 'order': o, 'from': f, 'to': to, 'actor': a}, sort_keys=True) + '\n'
                       for t, o, f, to, a in rows)

    def receive(self, order_id, message, *, actor='SYNTHETIC_CUSTOMER'):
        if not order_id.startswith('SYNTHETIC_'):
            raise ValueError('SYNTHETIC order ID required')
        row = self.db.execute('SELECT fields,status FROM orders WHERE id=?', (order_id,)).fetchone()
        fields, current = (json.loads(row[0]), State(row[1])) if row else ({}, State.NEW)
        fields.update(check_schema(self.parser.parse(message)))
        issues = validate(fields)
        status = next_state(current, Event.SUBMIT_INCOMPLETE if issues else Event.SUBMIT_COMPLETE)
        with self.db:
            self.db.execute('INSERT INTO orders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET fields=excluded.fields,status=excluded.status',
                            (order_id, json.dumps(fields, sort_keys=True), status))
            self._audit(order_id, current, status, actor)
        return {'classification': 'SYNTHETIC', 'order': order_id, 'status': status,
                'question': 'Please provide/correct: ' + ', '.join(issues) if issues else None}

    def decide(self, order_id, decision, *, actor):
        # Local demo role assertion, not real authentication.
        if actor != 'SYNTHETIC_OWNER':
            raise PermissionError('Only the simulated owner may decide')
        fields, status = self.get(order_id)
        if decision not in ('approve', 'reject', 'correct'):
            raise ValueError('Unknown decision')
        next_status = next_state(status, Event(decision))
        if validate(fields):  # defence in depth: state alone should already guarantee this
            raise ValueError('Complete order awaiting approval required')
        with self.db:
            self.db.execute('UPDATE orders SET status=? WHERE id=?', (next_status, order_id))
            self._audit(order_id, status, next_status, actor)
        return next_status

    def confirm(self, order_id):
        fields, status = self.get(order_id)
        if status != State.APPROVED or validate(fields):
            raise ValueError('Confirmation blocked: owner approval required')
        message = f'SYNTHETIC confirmation for {order_id}; local outbox only; nothing sent.'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox VALUES (?,?)', (order_id, message))
        return message

    def export_approved_csv(self):
        """Spreadsheet-compatible local export, not a Google Sheets connector."""
        output = io.StringIO(newline='')
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(('classification', 'order_id', *FIELDS, 'status'))
        rows = self.db.execute("SELECT id, fields FROM orders WHERE status='approved' ORDER BY id")
        for order_id, raw in rows:
            fields = json.loads(raw)
            if validate(fields):
                raise ValueError('Invalid approved order cannot be exported')
            # Prefix formula-leading values so spreadsheet software treats them as text.
            values = ['SYNTHETIC', order_id, *(fields[name] for name in FIELDS), 'approved']
            writer.writerow(["'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in values])
        return output.getvalue()

def run_demo(database=':memory:', audit_path=None, parser=None):
    fixture = json.loads(Path(__file__).with_name('synthetic_inputs.json').read_text(encoding='utf-8'))
    flow = Workflow(database, parser)
    try:
        print('SYNTHETIC DATA ONLY. No real customers, phone numbers, invoices, or network calls.')
        # Repeat runs use a fresh in-memory database by default.
        order_id = fixture['order_id']
        for message in fixture['messages']:
            result = flow.receive(order_id, message)
            print(json.dumps(result, sort_keys=True))
        try:
            flow.confirm(order_id)
        except ValueError as error:
            print(str(error))
        print('SIMULATED OWNER REQUEST:', flow.decide(order_id, 'correct', actor='SYNTHETIC_OWNER'))
        print(json.dumps(flow.receive(order_id, 'quantity=3'), sort_keys=True))
        print('SIMULATED OWNER DECISION:', flow.decide(order_id, 'approve', actor='SYNTHETIC_OWNER'))
        print(flow.confirm(order_id))
        print('SQLite orders:', flow.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0])
        print('Local outbox records:', flow.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0])
        print('SYNTHETIC approved-orders CSV (local output only):')
        print(flow.export_approved_csv(), end='')
        if audit_path:
            Path(audit_path).write_text(flow.export_audit_jsonl(), encoding='utf-8')
    finally:
        flow.close()
