"""rickshaw-ai — a unified, provider-agnostic LLM package.

Unified LLM API with provider collections, automatic auth resolution, token &
cost tracking, and simple context persistence + hand-off to other models
mid-session. Only tool-calling models are registered.

Quick start::

    from rickshaw_ai import builtin_models

    models = builtin_models()                       # env-based auth by default
    session = models.session(system="You are helpful.")
    result = await session.run("Hi", model="anthropic/claude-sonnet-4-20250514")
    result = await session.run("More", model="openai/gpt-4o")   # mid-session handoff
    print(session.usage.total.cost_usd)
"""

from rickshaw_ai.credentials import (
    ApiKeyCredential,
    Credential,
    CredentialStore,
    FileCredentialStore,
    InMemoryCredentialStore,
    OAuthCredential,
)
from rickshaw_ai.errors import (
    AuthError,
    ConnectionError,
    ContentFilterError,
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OverloadedError,
    ProviderError,
    RateLimitError,
    RickshawAIError,
    TimeoutError,
    ToolInputError,
)
from rickshaw_ai.factory import (
    ModelHandle,
    Models,
    ProviderHandle,
    builtin_models,
    create_models,
)
from rickshaw_ai.generate import (
    GenerateRequest,
    GenerateResult,
    Pricing,
    Reasoning,
    StopReason,
    Usage,
)
from rickshaw_ai.messages import (
    ImageBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from rickshaw_ai.registry import ModelInfo, OAuthConfig, ProviderInfo, RetryPolicy
from rickshaw_ai.session import Session, SessionUsage
from rickshaw_ai.tools import Tool, ToolCall, tool, validate_arguments

__version__ = "0.1.0"

__all__ = [
    # factory
    "create_models",
    "builtin_models",
    "Models",
    "ModelHandle",
    "ProviderHandle",
    # credentials
    "CredentialStore",
    "InMemoryCredentialStore",
    "FileCredentialStore",
    "Credential",
    "ApiKeyCredential",
    "OAuthCredential",
    # messages
    "Message",
    "TextBlock",
    "ImageBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    # tools
    "Tool",
    "ToolCall",
    "tool",
    "validate_arguments",
    # generate
    "GenerateRequest",
    "GenerateResult",
    "Usage",
    "Pricing",
    "Reasoning",
    "StopReason",
    # registry
    "ModelInfo",
    "ProviderInfo",
    "OAuthConfig",
    "RetryPolicy",
    # session
    "Session",
    "SessionUsage",
    # errors
    "RickshawAIError",
    "AuthError",
    "RateLimitError",
    "OverloadedError",
    "InvalidRequestError",
    "NotFoundError",
    "ContextLengthExceededError",
    "ContentFilterError",
    "TimeoutError",
    "ConnectionError",
    "ProviderError",
    "ToolInputError",
    "__version__",
]
