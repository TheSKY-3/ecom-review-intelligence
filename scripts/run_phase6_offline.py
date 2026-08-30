"""Run the P6 Agent without permitting live provider access."""

from ecom_review_intelligence.agent_cli import offline_main

if __name__ == "__main__":
    raise SystemExit(offline_main())
