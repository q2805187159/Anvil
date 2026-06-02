"""Context predictor for prefetching likely-needed context."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage

from .contracts import ContextType, PrefetchPrediction, Priority

if TYPE_CHECKING:
    from .contracts import JITContextConfig

logger = logging.getLogger(__name__)


class ContextPredictor:
    """Predicts which context will be needed next for prefetching.

    Strategies:
    - Pattern matching: Detect file paths, memory references, skill names
    - Conversation flow: Predict based on recent messages
    - Task analysis: Infer context needs from task type
    """

    def __init__(self, config: JITContextConfig):
        """Initialize context predictor.

        Args:
            config: JIT context configuration
        """
        self.config = config

        # Regex patterns for context detection
        self.file_pattern = re.compile(r'(?:^|\s)([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)(?:\s|$)')
        self.memory_pattern = re.compile(r'(?:remember|recall|memory|mentioned|said)\s+(?:about\s+)?([a-zA-Z0-9_\-\s]+)')
        self.skill_pattern = re.compile(r'(?:use|run|execute|call)\s+([a-zA-Z0-9_\-]+)(?:\s+skill)?')

    def predict(
        self,
        messages: list[BaseMessage],
        current_message: str | None = None
    ) -> list[PrefetchPrediction]:
        """Predict context that will likely be needed.

        Args:
            messages: Recent message history
            current_message: Current user message (if available)

        Returns:
            List of prefetch predictions sorted by confidence
        """
        if not self.config.prefetch_enabled:
            return []

        predictions: list[PrefetchPrediction] = []

        # Analyze current message
        if current_message:
            predictions.extend(self._analyze_message(current_message))

        # Analyze recent messages
        for msg in messages[-3:]:  # Last 3 messages
            content = self._extract_content(msg)
            if content:
                predictions.extend(self._analyze_message(content))

        # Deduplicate and sort by confidence
        predictions = self._deduplicate_predictions(predictions)
        predictions.sort(key=lambda p: p.confidence, reverse=True)

        # Apply threshold and limit
        predictions = [
            p for p in predictions
            if p.confidence >= self.config.prefetch_threshold
        ][:self.config.prefetch_max_items]

        if predictions:
            logger.info(f"Predicted {len(predictions)} prefetch candidates")
            for pred in predictions:
                logger.debug(
                    f"  - {pred.context_type}:{pred.identifier} "
                    f"(confidence: {pred.confidence:.2f}, reason: {pred.reason})"
                )

        return predictions

    def _analyze_message(self, content: str) -> list[PrefetchPrediction]:
        """Analyze message content for context references.

        Args:
            content: Message content

        Returns:
            List of predictions
        """
        predictions: list[PrefetchPrediction] = []

        # Detect file references
        file_matches = self.file_pattern.findall(content)
        for file_path in file_matches:
            predictions.append(PrefetchPrediction(
                context_type=ContextType.FILE,
                identifier=file_path,
                confidence=0.8,
                reason=f"File path mentioned: {file_path}",
                priority=Priority.MEDIUM
            ))

        # Detect memory references
        memory_matches = self.memory_pattern.findall(content)
        for memory_ref in memory_matches:
            predictions.append(PrefetchPrediction(
                context_type=ContextType.MEMORY,
                identifier=memory_ref.strip(),
                confidence=0.7,
                reason=f"Memory reference: {memory_ref}",
                priority=Priority.LOW
            ))

        # Detect skill references
        skill_matches = self.skill_pattern.findall(content)
        for skill_name in skill_matches:
            predictions.append(PrefetchPrediction(
                context_type=ContextType.SKILL,
                identifier=skill_name,
                confidence=0.75,
                reason=f"Skill mentioned: {skill_name}",
                priority=Priority.MEDIUM
            ))

        # Detect project context needs
        if any(keyword in content.lower() for keyword in ['readme', 'documentation', 'project', 'setup']):
            predictions.append(PrefetchPrediction(
                context_type=ContextType.PROJECT,
                identifier='README.md',
                confidence=0.6,
                reason="Project documentation keywords detected",
                priority=Priority.LOW
            ))

        return predictions

    def _deduplicate_predictions(
        self,
        predictions: list[PrefetchPrediction]
    ) -> list[PrefetchPrediction]:
        """Deduplicate predictions, keeping highest confidence.

        Args:
            predictions: List of predictions

        Returns:
            Deduplicated list
        """
        seen: dict[tuple[ContextType, str], PrefetchPrediction] = {}

        for pred in predictions:
            key = (pred.context_type, pred.identifier)
            if key not in seen or pred.confidence > seen[key].confidence:
                seen[key] = pred

        return list(seen.values())

    def _extract_content(self, message: BaseMessage) -> str:
        """Extract text content from message.

        Args:
            message: Message to extract from

        Returns:
            Extracted text content
        """
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
