"""Thin CLI: `python -m order_intake [--diagram]`."""
import argparse

from . import __doc__ as package_doc
from .parsers import KeyValueParser, LLMParser
from .workflow import diagram, run_demo


def main(argv=None):
    parser = argparse.ArgumentParser(prog='order_intake', description=package_doc)
    parser.add_argument('--diagram', action='store_true', help='print the Mermaid flow and exit')
    parser.add_argument('--audit-log', metavar='PATH', help='append one JSONL line per state change to PATH')
    parser.add_argument('--parser', choices=('keyvalue', 'llm'), default='keyvalue',
                        help='llm is opt-in and needs ORDER_INTAKE_ANTHROPIC_API_KEY')
    args = parser.parse_args(argv)
    if args.diagram:
        print(diagram())
    else:
        chosen = LLMParser.from_env() if args.parser == 'llm' else KeyValueParser()
        run_demo(audit_path=args.audit_log, parser=chosen)


if __name__ == '__main__':
    main()
