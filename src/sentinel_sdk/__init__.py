"""Sentinel Python SDK.

A minimal client for the Sentinel gateway. Designed to be a drop-in
replacement for the Anthropic SDK's ``messages.create`` shape, so
existing code can move behind the gateway with a one-line change:

>>> from sentinel_sdk import Sentinel
>>> client = Sentinel(api_key="sk_live_...", base_url="https://gateway.example.com")
>>> response = client.messages.create(
...     model="claude-sonnet-4-5",
...     max_tokens=500,
...     messages=[{"role": "user", "content": "Hello"}],
... )
>>> print(response.content)
"""

from sentinel_sdk.client import (
    Message,
    MessagesResponse,
    Sentinel,
    SentinelAPIError,
    SentinelAuthError,
    SentinelRateLimitError,
)

__all__ = [
    "Message",
    "MessagesResponse",
    "Sentinel",
    "SentinelAPIError",
    "SentinelAuthError",
    "SentinelRateLimitError",
]
__version__ = "0.1.0"
