"""The one place this app talks to Claude.

Every feature that uses Claude (bill photos, messy statements, the assistant)
builds a Messages API request body and hands it to call(). Nothing sent or
received is stored or logged here.
"""
import anthropic


class ClaudeError(Exception):
    """A failed call. kind is "busy" (rate limited), "timeout" or "failed"."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


def call(body, api_key, timeout):
    """Send one Messages API request; return the response as a plain dict."""
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
    try:
        return client.messages.create(**body).to_dict()
    except anthropic.RateLimitError:
        raise ClaudeError("busy")
    except anthropic.APITimeoutError:
        raise ClaudeError("timeout")
    except anthropic.APIConnectionError:
        raise ClaudeError("timeout")
    except anthropic.APIStatusError:
        raise ClaudeError("failed")
