"""Run the P6 live boundary only after all frozen-gate checks pass."""

from ecom_review_intelligence.agent_cli import live_main

if __name__ == "__main__":
    raise SystemExit(live_main())
