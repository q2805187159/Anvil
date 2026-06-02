"""Analyzer for extracting critical facts from messages."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .contracts import CriticalFact

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


class ContentAnalyzer:
    """Analyzes message content to extract critical facts.

    Critical facts that must be preserved:
    - Architectural decisions
    - Unresolved bugs/issues
    - Key implementation details
    - Active constraints
    - Important file paths or identifiers
    """

    # Patterns for different fact types
    DECISION_PATTERNS = [
        r"decided to (use|implement|choose|adopt)",
        r"architectural decision",
        r"we will (use|implement|adopt)",
    ]

    BUG_PATTERNS = [
        r"(bug|issue|error|problem|failure) in",
        r"(unresolved|outstanding|open) (bug|issue)",
        r"needs to be fixed",
    ]

    CONSTRAINT_PATTERNS = [
        r"(must|should|required to|constraint)",
        r"(cannot|must not|should not)",
        r"requirement:",
    ]

    IMPLEMENTATION_PATTERNS = [
        r"implemented (in|at|using)",
        r"(function|class|method) (called|named)",
        r"(uses|relies on|depends on)",
    ]

    IDENTIFIER_PATTERNS = [
        r"(/[\w/.-]+\.\w+)",  # File paths
        r"(\w+\.\w+\.\w+)",    # Module paths
        r"(https?://[^\s]+)",  # URLs
    ]

    def __init__(self):
        """Initialize analyzer with compiled patterns."""
        self._decision_regex = re.compile("|".join(self.DECISION_PATTERNS), re.IGNORECASE)
        self._bug_regex = re.compile("|".join(self.BUG_PATTERNS), re.IGNORECASE)
        self._constraint_regex = re.compile("|".join(self.CONSTRAINT_PATTERNS), re.IGNORECASE)
        self._implementation_regex = re.compile("|".join(self.IMPLEMENTATION_PATTERNS), re.IGNORECASE)
        self._identifier_regex = re.compile("|".join(self.IDENTIFIER_PATTERNS))

    def extract_critical_facts(self, messages: list[BaseMessage]) -> list[CriticalFact]:
        """Extract critical facts from messages.

        Args:
            messages: List of messages to analyze

        Returns:
            List of critical facts with metadata
        """
        facts: list[CriticalFact] = []

        for i, msg in enumerate(messages):
            content = self._extract_content(msg)

            # Extract facts by type
            facts.extend(self._extract_decisions(content, i))
            facts.extend(self._extract_bugs(content, i))
            facts.extend(self._extract_constraints(content, i))
            facts.extend(self._extract_implementations(content, i))
            facts.extend(self._extract_identifiers(content, i))

        # Deduplicate facts
        return self._deduplicate_facts(facts)

    def _extract_content(self, message: BaseMessage) -> str:
        """Extract text content from message."""
        content = getattr(message, "content", "")
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

    def _extract_decisions(self, content: str, index: int) -> list[CriticalFact]:
        """Extract architectural decisions."""
        facts = []
        for match in self._decision_regex.finditer(content):
            # Extract sentence containing the decision
            sentence = self._extract_sentence(content, match.start())
            if sentence:
                facts.append(CriticalFact(
                    content=sentence,
                    source_message_index=index,
                    fact_type="decision",
                    confidence=0.9
                ))
        return facts

    def _extract_bugs(self, content: str, index: int) -> list[CriticalFact]:
        """Extract bug/issue mentions."""
        facts = []
        for match in self._bug_regex.finditer(content):
            sentence = self._extract_sentence(content, match.start())
            if sentence:
                facts.append(CriticalFact(
                    content=sentence,
                    source_message_index=index,
                    fact_type="bug",
                    confidence=0.95
                ))
        return facts

    def _extract_constraints(self, content: str, index: int) -> list[CriticalFact]:
        """Extract constraints/requirements."""
        facts = []
        for match in self._constraint_regex.finditer(content):
            sentence = self._extract_sentence(content, match.start())
            if sentence:
                facts.append(CriticalFact(
                    content=sentence,
                    source_message_index=index,
                    fact_type="constraint",
                    confidence=0.85
                ))
        return facts

    def _extract_implementations(self, content: str, index: int) -> list[CriticalFact]:
        """Extract implementation details."""
        facts = []
        for match in self._implementation_regex.finditer(content):
            sentence = self._extract_sentence(content, match.start())
            if sentence:
                facts.append(CriticalFact(
                    content=sentence,
                    source_message_index=index,
                    fact_type="implementation",
                    confidence=0.8
                ))
        return facts

    def _extract_identifiers(self, content: str, index: int) -> list[CriticalFact]:
        """Extract important identifiers (file paths, URLs, etc.)."""
        facts = []
        for match in self._identifier_regex.finditer(content):
            identifier = match.group(0)
            facts.append(CriticalFact(
                content=f"Important identifier: {identifier}",
                source_message_index=index,
                fact_type="identifier",
                confidence=1.0
            ))
        return facts

    def _extract_sentence(self, text: str, position: int) -> str | None:
        """Extract sentence containing the given position."""
        # Find sentence boundaries
        sentences = re.split(r'[.!?]\s+', text)

        current_pos = 0
        for sentence in sentences:
            sentence_end = current_pos + len(sentence)
            if current_pos <= position <= sentence_end:
                return sentence.strip()
            current_pos = sentence_end + 2  # Account for delimiter

        return None

    def _deduplicate_facts(self, facts: list[CriticalFact]) -> list[CriticalFact]:
        """Remove duplicate facts based on content similarity."""
        if not facts:
            return []

        unique_facts = []
        seen_content = set()

        for fact in facts:
            # Normalize content for comparison
            normalized = fact.content.lower().strip()

            # Check if we've seen similar content
            if normalized not in seen_content:
                unique_facts.append(fact)
                seen_content.add(normalized)

        return unique_facts
