"""Orchestrator — owns the turn loop.

The only hot-path caller of the provider. Depends on LLMProvider via
dependency injection, forwards Effort, advertises memory tool specs,
and dispatches returned tool calls.
"""

from __future__ import annotations

import logging

from rickshaw.memory.service import MemoryService
from rickshaw.memory.tools import MEMORY_TOOL_SPECS, dispatch_tool_call
from rickshaw.prompt.builder import PromptBuilder
from rickshaw.providers.base import Effort, LLMProvider, Message
from rickshaw.queue import Job, JobQueue, JobType

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 3
_DEFAULT_SYSTEM = (
    "You are a helpful assistant with access to a semantic memory layer. "
    "Use the provided tools to remember, recall, or forget information."
)


class Orchestrator:
    """Turn loop with memory-augmented retrieval and tool dispatch.

    Degrades gracefully if:
    * The provider is unreachable (still allows local remember/recall/ranking).
    * The provider reports ``function_calling=False`` (skips tool advertising).
    """

    def __init__(
        self,
        provider: LLMProvider,
        memory: MemoryService,
        prompt_builder: PromptBuilder | None = None,
        queue: JobQueue | None = None,
        system: str = _DEFAULT_SYSTEM,
        effort: Effort = Effort.MEDIUM,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
    ) -> None:
        self.provider = provider
        self.memory = memory
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.queue = queue or JobQueue()
        self.system = system
        self.effort = effort
        self.max_tool_rounds = max_tool_rounds

    def run_turn(self, task_input: str) -> str:
        """Execute a single conversational turn.

        1. Assemble context from memory.
        2. Build the prompt.
        3. Call the provider (with tool specs if supported).
        4. Dispatch any tool calls; loop up to *max_tool_rounds*.
        5. Write observations to memory.
        6. Enqueue deferred jobs (importance scoring).
        7. Return the final text.
        """
        ctx = self.memory.assemble_context(task_input)

        caps = self.provider.capabilities()
        use_tools = caps.function_calling
        tool_specs = MEMORY_TOOL_SPECS if use_tools else None

        messages = self.prompt_builder.build(
            system=self.system,
            tools=tool_specs,
            context=ctx,
            task_input=task_input,
        )

        try:
            response = self.provider.complete(
                messages, effort=self.effort, tools=tool_specs,
            )
        except Exception as exc:
            logger.warning("Provider call failed: %s", exc)
            # Degrade: still do local retrieval
            results = self.memory.recall(task_input)
            if results:
                return "Provider unavailable. From memory: " + "; ".join(
                    r["text"] for r in results
                )
            return f"Provider unavailable: {exc}"

        # Tool-call dispatch loop
        for _ in range(self.max_tool_rounds):
            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                result = dispatch_tool_call(tc, self.memory)
                messages.append(Message(
                    role="assistant",
                    content=f"[tool_call: {tc.name}({tc.arguments})]",
                ))
                messages.append(Message(role="tool", content=result))

            try:
                response = self.provider.complete(
                    messages, effort=self.effort, tools=tool_specs,
                )
            except Exception as exc:
                logger.warning("Follow-up provider call failed: %s", exc)
                break

        # Write observations
        records = self.memory.write_observations(response)

        # Enqueue deferred jobs
        for rec in records:
            self.queue.enqueue(Job(
                type=JobType.IMPORTANCE_SCORING,
                payload={"record_id": rec.id},
            ))

        return response.text
