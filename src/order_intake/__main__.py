"""Thin CLI: `python -m order_intake [--diagram]`."""
import argparse

from . import __doc__ as package_doc
from .workflow import diagram, run_demo


def main(argv=None):
    parser = argparse.ArgumentParser(prog='order_intake', description=package_doc)
    parser.add_argument('--diagram', action='store_true', help='print the Mermaid flow and exit')
    args = parser.parse_args(argv)
    if args.diagram:
        print(diagram())
    else:
        run_demo()


if __name__ == '__main__':
    main()
