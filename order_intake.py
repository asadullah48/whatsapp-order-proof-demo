"""Offline SYNTHETIC order workflow. Python standard library only."""
import csv
import io
import json
import sqlite3
from pathlib import Path

FIELDS = ('item', 'quantity', 'size', 'address', 'phone')
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

def parse_fields(message):
    """Explicit key=value grammar; never pretend to understand arbitrary prose."""
    result = {}
    for chunk in message.split(';'):
        key, separator, value = chunk.strip().partition('=')
        if not separator or key not in FIELDS or key in result:
            raise ValueError('Use unique item/quantity/size/address/phone key=value fields')
        if value.strip():
            result[key] = value.strip()
    return result

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
    def __init__(self, database=':memory:'):
        self.db = sqlite3.connect(database)
        self.db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, fields TEXT NOT NULL, status TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (order_id TEXT PRIMARY KEY REFERENCES orders(id), message TEXT NOT NULL)')

    def close(self):
        self.db.close()

    def get(self, order_id):
        row = self.db.execute('SELECT fields,status FROM orders WHERE id=?', (order_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown order')
        return json.loads(row[0]), row[1]

    def receive(self, order_id, message):
        if not order_id.startswith('SYNTHETIC_'):
            raise ValueError('SYNTHETIC order ID required')
        row = self.db.execute('SELECT fields,status FROM orders WHERE id=?', (order_id,)).fetchone()
        if row and row[1] in ('approved', 'rejected'):
            raise ValueError('Terminal order cannot be edited')
        fields = json.loads(row[0]) if row else {}
        fields.update(parse_fields(message))
        issues = validate(fields)
        status = 'waiting_for_details' if issues else 'waiting_for_approval'
        with self.db:
            self.db.execute('INSERT INTO orders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET fields=excluded.fields,status=excluded.status',
                            (order_id, json.dumps(fields, sort_keys=True), status))
        return {'classification': 'SYNTHETIC', 'order': order_id, 'status': status,
                'question': 'Please provide/correct: ' + ', '.join(issues) if issues else None}

    def decide(self, order_id, decision, *, actor):
        # Local demo role assertion, not real authentication.
        if actor != 'SYNTHETIC_OWNER':
            raise PermissionError('Only the simulated owner may decide')
        fields, status = self.get(order_id)
        if decision not in ('approve', 'reject', 'correct'):
            raise ValueError('Unknown decision')
        if status != 'waiting_for_approval' or validate(fields):
            raise ValueError('Complete order awaiting approval required')
        next_status = {'approve': 'approved', 'reject': 'rejected', 'correct': 'waiting_for_details'}[decision]
        with self.db:
            self.db.execute('UPDATE orders SET status=? WHERE id=?', (next_status, order_id))
        return next_status

    def confirm(self, order_id):
        fields, status = self.get(order_id)
        if status != 'approved' or validate(fields):
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

def run_demo(database=':memory:'):
    fixture = json.loads(Path(__file__).with_name('synthetic_inputs.json').read_text(encoding='utf-8'))
    flow = Workflow(database)
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
    finally:
        flow.close()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagram', action='store_true')
    args = parser.parse_args()
    print(diagram()) if args.diagram else run_demo()
