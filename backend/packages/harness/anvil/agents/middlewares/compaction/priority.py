"""Priority classification for messages during compaction."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .contracts import ClassifiedMessages

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


class PriorityClassifier:
    """Classifies messages into HIGH/MEDIUM/LOW priority tiers.

    Classification rules based on JavaGuide Context Engineering principles:

    HIGH PRIORITY (never discard):
    - System messages
    - Messages with critical markers (CRITICAL, IMPORTANT, DECISION)
    - Unresolved error messages
    - Active task definitions

    MEDIUM PRIORITY (trim if needed, keep summaries):
    - Recent messages (last N messages)
    - Non-redundant tool results
    - Memory injection messages

    LOW PRIORITY (aggressive compression):
    - Early conversation history
    - Redundant tool results
    - Verbose explanations
    - Intermediate exploration results
    """

    # Patterns indicating high-priority content
    CRITICAL_PATTERNS = [
        r"\b(CRITICAL|IMPORTANT|DECISION|UNRESOLVED|ERROR|FAILED|BUG)\b",
        r"\b(architectural decision|key implementation|active constraint)\b",
        r"\b(must preserve|do not discard|critical information)\b",
    ]

    # Patterns indicating redundant/verbose content
    REDUNDANT_PATTERNS = [
        r"^(Listing|Reading|Searching|Exploring)",  # Exploration verbs
        r"(successfully completed|operation successful)",  # Redundant success messages
        r"^(Here is|Here are|I found|I see)",  # Verbose preambles
    ]

    def __init__(self, min_recent_messages: int = 10):
        """Initialize classifier.

        Args:
            min_recent_messages: Number of recent messages to always keep
        """
        self.min_recent_messages = min_recent_messages
        self._critical_regex = re.compile(
            "|".join(self.CRITICAL_PATTERNS),
            re.IGNORECASE
        )
        self._redundant_regex = re.compile(
            "|".join(self.REDUNDANT_PATTERNS),
            re.IGNORECASE
        )

    def classify_messages(self, messages: list[BaseMessage]) -> ClassifiedMessages:
        """Classify messages into priority tiers.

        Args:
            messages: List of messages to classify

        Returns:
            ClassifiedMessages with messages sorted by priority
        """
        high_priority = []
        medium_priority = []
        low_priority = []

        total_count = len(messages)
        recent_threshold = max(0, total_count - self.min_recent_messages)

        # Track tool calls to detect redundancy
        tool_call_counts: dict[str, int] = {}

        for i, msg in enumerate(messages):
            # Always high priority: System messages
            if isinstance(msg, SystemMessage):
                high_priority.append(msg)
                continue

            # Check if message is recent (always medium priority minimum)
            is_recent = i >= recent_threshold

            # Check for critical content markers
            content = self._extract_content(msg)
            is_critical = self._is_critical_content(content)

            if is_critical:
                high_priority.append(msg)
            elif is_recent:
                medium_priority.append(msg)
            elif isinstance(msg, ToolMessage):
                # Check for redundant tool results
                tool_name = self._extract_tool_name(msg)
                tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1

                if tool_call_counts[tool_name] <= 2:
                    # First 2 calls of each tool are medium priority
                    medium_priority.append(msg)
                else:
                    # Subsequent calls are low priority (likely redundant)
                    low_priority.append(msg)
            elif self._is_verbose_explanation(content):
                low_priority.append(msg)
            else:
                # Default: medium priority
                medium_priority.append(msg)

        return ClassifiedMessages(
            high_priority=high_priority,
            medium_priority=medium_priority,
            low_priority=low_priority
        )

    def _extract_content(self, message: BaseMessage) -> str:
        """Extract text content from message."""
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Handle multi-part content
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
            return " ".join(parts)
        return str(content)

    def _is_critical_content(self, content: str) -> bool:
        """Check if content contains critical markers."""
        return bool(self._critical_regex.search(content))

    def _is_verbose_explanation(self, content: str) -> bool:
        """Check if content is verbose/redundant."""
        return bool(self._redundant_regex.search(content))

    def _extract_tool_name(self, message: ToolMessage) -> str:
        """Extract tool name from ToolMessage."""
        # Try to get tool name from message attributes
        if hasattr(message, "name"):
            return str(message.name)
        if hasattr(message, "tool_call_id"):
            # Extract tool name from tool_call_id if present
            tool_call_id = str(message.tool_call_id)
            if "_" in tool_call_id:
                return tool_call_id.split("_")[0]
        return "unknown_tool"
