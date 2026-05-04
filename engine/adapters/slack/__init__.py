"""Slack source adapter — thread/channel fetch via the Slack Web API."""

from engine.adapters.slack.extractor import (
    DEFAULT_API_BASE,
    SlackExtractError,
    extract_slack,
    parse_slack_url,
)

__all__ = [
    "DEFAULT_API_BASE",
    "SlackExtractError",
    "extract_slack",
    "parse_slack_url",
]
