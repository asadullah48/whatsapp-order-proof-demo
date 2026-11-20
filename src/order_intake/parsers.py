"""Message parsers. Every parser's output passes check_schema before the workflow uses it."""
import json
import os
from typing import Protocol

FIELDS = ('item', 'quantity', 'size', 'address', 'phone')
LLM_KEY_ENV = 'ORDER_INTAKE_ANTHROPIC_API_KEY'  # dedicated name so a global key never turns this on


class MessageParser(Protocol):
    def parse(self, message: str) -> dict:
        """Return proposed {field: value}; output is untrusted until check_schema passes."""


def parse_fields(message):
    """Explicit key=value grammar; never pretend to understand arbitrary prose."""
    result = {}
    for chunk in message.split(';'):
        key, separator, value = chunk.strip().partition('=')
        if not separator or key not in FIELDS or key in result:
            raise ValueError('Use unique item/quantity/size/address/phone key=value fields')
        if value.strip():
            result[key] = value.strip()
    return result


def check_schema(fields):
    """Shape check shared by all parsers: known keys only, non-empty string values."""
    if not isinstance(fields, dict):
        raise ValueError('Parser output must be an object')
    clean = {}
    for key, value in fields.items():
        if key not in FIELDS:
            raise ValueError(f'Parser output has unknown field: {key!r}')
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Parser output field {key!r} must be a non-empty string')
        clean[key] = value.strip()
    return clean


class KeyValueParser:
    """Default parser: deterministic key=value grammar."""

    def parse(self, message):
        return parse_fields(message)


class LLMParser:
    """Optional free-text parser via the Anthropic API. OFF by default.

    Needs `pip install .[llm]` and the ORDER_INTAKE_ANTHROPIC_API_KEY env var.
    Never executed in this repo's tests or CI; no live call has been verified.
    """

    SYSTEM = ('Extract order fields from the customer message. Reply with only a JSON object '
              'whose keys are a subset of ' + ', '.join(FIELDS) + ' and whose values are strings. '
              'Omit fields the message does not state. Never add other keys.')

    def __init__(self, api_key, model='claude-opus-5-5'):
        import anthropic  # optional dependency, imported only when the LLM parser is chosen
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    @classmethod
    def from_env(cls, env=None):
        key = (os.environ if env is None else env).get(LLM_KEY_ENV)
        if not key:
            raise RuntimeError(f'{LLM_KEY_ENV} is not set; the LLM parser stays off')
        return cls(key)

    def parse(self, message):
        response = self.client.messages.create(
            model=self.model, max_tokens=1024, system=self.SYSTEM,
            messages=[{'role': 'user', 'content': message}])
        if response.stop_reason == 'refusal':
            raise ValueError('LLM parser declined the message')
        text = ''.join(block.text for block in response.content if block.type == 'text')
        try:
            return json.loads(text)  # untrusted: Workflow.receive runs check_schema + validate
        except json.JSONDecodeError:
            raise ValueError('LLM parser did not return JSON') from None
