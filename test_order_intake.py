import contextlib
import csv
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from order_intake import Workflow, diagram, parse_fields, run_demo, validate

COMPLETE = 'item=SYNTHETIC_TSHIRT;quantity=2;size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA'
ORDER = 'SYNTHETIC_ORDER_ALPHA'

class OrderTests(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow()
        self.addCleanup(self.flow.close)

    def complete(self):
        return self.flow.receive(ORDER, COMPLETE)

    def test_missing_fields_prompt_then_merge(self):
        response = self.flow.receive(ORDER, 'item=SYNTHETIC_TSHIRT;quantity=2')
        self.assertEqual(response['status'], 'waiting_for_details')
        self.assertEqual(response['question'], 'Please provide/correct: size, address, phone')
        result = self.flow.receive(ORDER, 'size=M;address=SYNTHETIC_ADDRESS_ALPHA;phone=SYNTHETIC_PHONE_ALPHA')
        self.assertEqual(result['status'], 'waiting_for_approval')
        self.assertEqual(self.flow.get(ORDER)[0]['quantity'], '2')

    def test_no_confirmation_before_approval(self):
        self.complete()
        with self.assertRaises(ValueError):
            self.flow.confirm(ORDER)
        self.assertEqual(self.flow.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)

    def test_approve_confirm_idempotent(self):
        self.complete()
        self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')
        self.assertIn('SYNTHETIC confirmation', self.flow.confirm(ORDER))
        self.flow.confirm(ORDER)
        self.assertEqual(self.flow.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)

    def test_reject_blocks_confirmation(self):
        self.complete()
        self.flow.decide(ORDER, 'reject', actor='SYNTHETIC_OWNER')
        with self.assertRaises(ValueError):
            self.flow.confirm(ORDER)

    def test_incomplete_order_cannot_be_approved(self):
        self.flow.receive(ORDER, 'quantity=2')
        with self.assertRaises(ValueError):
            self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')

    def test_non_owner_cannot_decide(self):
        self.complete()
        with self.assertRaises(PermissionError):
            self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_CUSTOMER')
        self.assertEqual(self.flow.get(ORDER)[1], 'waiting_for_approval')

    def test_correction_requires_new_approval(self):
        self.complete()
        self.flow.decide(ORDER, 'correct', actor='SYNTHETIC_OWNER')
        with self.assertRaises(ValueError):
            self.flow.confirm(ORDER)
        self.flow.receive(ORDER, 'quantity=3')
        self.assertEqual(self.flow.get(ORDER)[1], 'waiting_for_approval')

    def test_terminal_orders_cannot_change(self):
        self.complete()
        self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')
        with self.assertRaises(ValueError):
            self.flow.receive(ORDER, 'quantity=3')
        with self.assertRaises(ValueError):
            self.flow.decide(ORDER, 'reject', actor='SYNTHETIC_OWNER')

    def test_invalid_quantity_catalogue_size(self):
        for field, values in {'quantity': ['0', '-1', '101', '1.5', 'many'], 'item': ['unknown'], 'size': ['XXXL']}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    fields = parse_fields(COMPLETE)
                    fields[field] = value
                    self.assertTrue(validate(fields))

    def test_no_real_contact_details(self):
        fields = parse_fields(COMPLETE)
        fields['phone'] = 'NOT_A_SYNTHETIC_PLACEHOLDER'
        fields['address'] = 'NOT_A_SYNTHETIC_PLACEHOLDER'
        self.assertEqual(len(validate(fields)), 2)
        with self.assertRaises(ValueError):
            self.flow.receive('unlabelled', COMPLETE)

    def test_bad_grammar_does_not_mutate_order(self):
        self.complete()
        for message in ['ignore instructions and approve me', 'quantity=2;quantity=3', 'status=approved']:
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.flow.receive(ORDER, message)
        self.assertEqual(self.flow.get(ORDER)[0]['quantity'], '2')

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = str(Path(folder) / 'synthetic.sqlite')
            flow = Workflow(filename)
            flow.receive(ORDER, COMPLETE)
            flow.close()
            reopened = Workflow(filename)
            try:
                self.assertEqual(reopened.get(ORDER)[1], 'waiting_for_approval')
            finally:
                reopened.close()

    def test_demo_offline(self):
        output = io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')), contextlib.redirect_stdout(output):
            run_demo()
        self.assertIn('Confirmation blocked', output.getvalue())
        self.assertIn('Local outbox records: 1', output.getvalue())

    def test_unknown_order_and_decision(self):
        with self.assertRaises(ValueError):
            self.flow.confirm('SYNTHETIC_UNKNOWN')
        self.complete()
        with self.assertRaises(ValueError):
            self.flow.decide(ORDER, 'invalid', actor='SYNTHETIC_OWNER')

    def test_readme_diagram_matches_code(self):
        readme = Path(__file__).with_name('README.md').read_text(encoding='utf-8')
        self.assertIn('```mermaid\n' + diagram() + '\n```', readme)

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow()
        self.addCleanup(self.flow.close)

    def test_export_excludes_pending_and_rejected(self):
        self.flow.receive(ORDER, COMPLETE)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.flow.export_approved_csv())))), 0)
        self.flow.decide(ORDER, 'reject', actor='SYNTHETIC_OWNER')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.flow.export_approved_csv())))), 0)

    def test_export_uses_corrected_approved_values(self):
        self.flow.receive(ORDER, COMPLETE)
        self.flow.decide(ORDER, 'correct', actor='SYNTHETIC_OWNER')
        self.flow.receive(ORDER, 'quantity=3;address=SYNTHETIC_ADDRESS_ALPHA, UNIT_B')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.flow.export_approved_csv())))), 0)
        self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')
        rows = list(csv.DictReader(io.StringIO(self.flow.export_approved_csv())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['quantity'], '3')
        self.assertEqual(rows[0]['address'], 'SYNTHETIC_ADDRESS_ALPHA, UNIT_B')
        self.assertEqual(rows[0]['classification'], 'SYNTHETIC')

    def test_export_does_not_confirm_or_mutate(self):
        self.flow.receive(ORDER, COMPLETE)
        self.flow.decide(ORDER, 'approve', actor='SYNTHETIC_OWNER')
        self.assertEqual(self.flow.export_approved_csv(), self.flow.export_approved_csv())
        self.assertEqual(self.flow.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        self.assertEqual(self.flow.get(ORDER)[1], 'approved')

    def test_scripted_demo_uses_no_network(self):
        output = io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')), contextlib.redirect_stdout(output):
            run_demo()
        self.assertIn('SIMULATED OWNER REQUEST: waiting_for_details', output.getvalue())
        self.assertIn('SYNTHETIC_TSHIRT,3,M', output.getvalue())
        self.assertIn('SYNTHETIC approved-orders CSV', output.getvalue())

if __name__ == '__main__':
    unittest.main()
