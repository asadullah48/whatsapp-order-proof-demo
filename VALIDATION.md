# Validation

VERIFIED 4 Nov 2026, Python 3.14.7. No existing tests were weakened or removed.

| Scope | Exact command (from repo root) | Before | After |
| --- | --- | --- | --- |
| New sibling demo, copied intake baseline then extended | `python -B -m unittest discover -v` | 15 passed (0.083s) | 19 passed (0.091s) |
| Original whatsapp-order-intake-demo, unchanged | `python -B -m unittest discover -v` | 15 passed (0.089s) | 15 passed (0.089s) |
| hackathon-completion-engine at ab57f0edc089139b3f0ec9755596a068e16fb712 | `python -B -m pytest -q` | 51 passed, 5 warnings (17.55s) | 51 passed, 5 warnings (14.25s) |
| guardrailai at 8112ddd8082538b414073c5291f7f6506e07acc7 | `python -B -m pytest -q` | 53 passed (29.33s) | 53 passed (29.79s) |

The new-demo baseline and first after run used unittest's equivalent absolute
`-s D:\freelance\whatsapp-order-proof-demo` discovery argument. Final validation
runs use the repository-root command shown above.
The offers repo contains only offers.md, so it has no test suite. An initial
`python -m unittest discover -v` there reported `Ran 0 tests` / `NO TESTS RAN`;
this is not counted as a passing suite.
The broader proof-repo commands run their full configured root suites; the
hackathon repo's separately nested projects are not part of its root testpaths.
Existing hackathon warnings concern deprecated FastAPI startup handlers and
an invalid escape in a watcher docstring. Pytest also emitted an installed
LangSmith/Pydantic compatibility warning under Python 3.14.

The demo suite covers validation, missing fields, correction merging,
confirmation gating, rejection, terminal states, persistence, duplicate outbox
inserts, CSV exclusion/quoting/corrected values, read-only export, and a demo run
with socket creation blocked. No real services or API keys were used by the demo.

The README output was captured by subprocess from the actual demo command,
and its Mermaid text was emitted by the code's --diagram command.

VERIFIED 4 Nov 2026 at code commit `e00fa9db6239fbc818045d3669535109f84ea713`:
`python -B -m unittest discover -v` from the new repository root: **19 tests passed in 0.120s, OK**.
