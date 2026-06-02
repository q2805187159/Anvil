"""LLM-based compression for message history."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from anvil.agents.model_factory import create_chat_model
from anvil.config.service import resolve_internal_task_model_config

from .contracts import CriticalFact

if TYPE_CHECKING:
    from anvil.config import EffectiveConfig

logger = logging.getLogger(__name__)


class LLMCompressor:
    """Uses LLM to compress message history while preserving critical facts.

    Based on JavaGuide Context Engineering principles:
    - Preserve: Architectural decisions, unresolved bugs, key implementation details
    - Discard: Redundant tool results, verbose explanations, intermediate steps
    """

    COMPRESSION_PROMPT = """You are compressing conversation history for an AI agent.

Your task: Create a dense summary that preserves ONLY critical information.

ALWAYS PRESERVE:
- Architectural decisions made
- Unresolved bugs or issues
- Key implementation details
- Active constraints or requirements
- Important file paths or identifiers

ALWAYS DISCARD:
- Redundant tool call results
- Verbose explanations
- Intermediate exploration steps
- Superseded information
- Successful completion messages

Critical facts to preserve:
{critical_facts}

Input conversation:
{conversation}

Output a compressed summary in 200-400 tokens that captures all critical information.
Use concise, factual language. Group related information together."""

    def __init__(
        self,
        effective_config: EffectiveConfig | None = None,
        model_name: str | None = None,
        timeout_seconds: float = 30.0
    ):
        """Initialize compressor.

        Args:
            effective_config: Application configuration
            model_name: Model to use for compression (None = default)
            timeout_seconds: Timeout for compression operation
        """
        self.effective_config = effective_config
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def compress(
        self,
        messages: list[BaseMessage],
        preserve_facts: list[CriticalFact],
        max_summary_tokens: int
    ) -> BaseMessage:
        """Compress messages using LLM while preserving critical facts.

        Args:
            messages: Messages to compress
            preserve_facts: Critical facts that must be preserved
            max_summary_tokens: Maximum tokens for summary

        Returns:
            SystemMessage containing compressed summary
        """
        if not messages:
            return SystemMessage(content="[COMPRESSED CONTEXT: Empty]\n")

        # Format conversation for compression
        conversation_text = self._format_messages(messages)
        facts_text = self._format_facts(preserve_facts)

        # Call LLM for compression with timeout
        try:
            compressed_content = self._invoke_compression_model(
                conversation_text,
                facts_text,
                max_summary_tokens
            )
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            # Fallback: Simple truncation
            compressed_content = self._fallback_compression(messages, max_summary_tokens)

        # Create compressed message
        return SystemMessage(
            content=f"[COMPRESSED CONTEXT]\n{compressed_content}\n[END COMPRESSED CONTEXT]"
        )

    def _format_messages(self, messages: list[BaseMessage]) -> str:
        """Format messages for compression prompt."""
        lines = []
        for i, msg in enumerate(messages):
            msg_type = msg.__class__.__name__.replace("Message", "")
            content = self._extract_content(msg)

            # Truncate very long messages
            if len(content) > 1000:
                content = content[:1000] + "..."

            lines.append(f"[{i}] {msg_type}: {content}")

        return "\n".join(lines)

    def _format_facts(self, facts: list[CriticalFact]) -> str:
        """Format critical facts for compression prompt."""
        if not facts:
            return "(No critical facts identified)"

        lines = []
        for fact in facts:
            lines.append(f"- [{fact.fact_type}] {fact.content}")

        return "\n".join(lines)

    def _invoke_compression_model(
        self,
        conversation: str,
        facts: str,
        max_tokens: int
    ) -> str:
        """Invoke LLM for compression with timeout."""
        # Get model
        model = self._get_compression_model()

        # Format prompt
        prompt = self.COMPRESSION_PROMPT.format(
            conversation=conversation,
            critical_facts=facts
        )

        # Execute with timeout
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anvil-compaction")
        future = executor.submit(self._call_model, model, prompt)

        try:
            response = future.result(timeout=self.timeout_seconds)
            return self._extract_content(response)
        except FutureTimeoutError:
            logger.warning(f"Compression timed out after {self.timeout_seconds}s")
            future.cancel()
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _call_model(self, model, prompt: str):
        """Call model (executed in thread pool)."""
        return model.invoke(prompt)

    def _get_compression_model(self):
        """Get model for compression."""
        if self.effective_config is None:
            raise ValueError("EffectiveConfig required for compression")

        model_name = self.model_name or self.effective_config.default_model
        model_config = resolve_internal_task_model_config(
            self.effective_config,
            model_name
        )

        if model_config is None:
            model_config = self.effective_config.models.get(model_name)

        if model_config is None:
            raise ValueError(f"Model not found: {model_name}")

        # Create model with thinking disabled for compression
        return create_chat_model(model_config, thinking_enabled=False)

    def _fallback_compression(self, messages: list[BaseMessage], max_tokens: int) -> str:
        """Fallback compression using simple truncation."""
        logger.info("Using fallback compression (simple truncation)")

        # Extract key information from messages
        summaries = []
        for msg in messages[-5:]:  # Last 5 messages
            content = self._extract_content(msg)
            if len(content) > 200:
                content = content[:200] + "..."
            summaries.append(content)

        return "Compressed context (fallback):\n" + "\n".join(summaries)

    def _extract_content(self, message) -> str:
        """Extract text content from message."""
        if isinstance(message, BaseMessage):
            content = getattr(message, "content", "")
        else:
            content = getattr(message, "content", str(message))

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
            return " ".join(parts)
        return str(content)
