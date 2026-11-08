"""Run the evals. The logic lives in src/coach/evals.py, where it is typed and tested.

    uv run python evals/run_evals.py --dry-run   # check the cases: no LLM calls, no cost
    uv run python evals/run_evals.py             # run every case (costs money)
    uv run python evals/run_evals.py --case three-week-layoff --repeats 3
"""

import sys

from coach.evals import main

if __name__ == "__main__":
    sys.exit(main())
