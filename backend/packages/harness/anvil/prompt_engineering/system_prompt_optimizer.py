"""System prompt optimizer for token reduction."""

from __future__ import annotations

import re
from typing import Any

from .contracts import (
    OptimizationMetrics,
    OptimizationStrategy,
    OptimizedDescription,
    PromptEngineeringConfig,
    SystemPromptTemplate,
)


class SystemPromptOptimizer:
    """Optimizes system prompts to reduce token usage.

    Removes:
    - Personality fluff
    - Thinking instructions
    - Redundant capability listings
    - Verbose explanations

    Keeps:
    - Essential role definition
    - Core capabilities
    - Critical constraints
    - Minimal context
    """

    def __init__(self, config: PromptEngineeringConfig):
        """Initialize optimizer.

        Args:
            config: Prompt engineering configuration
        """
        self.config = config
        self.metrics = OptimizationMetrics()

    def optimize(self, system_prompt: str) -> OptimizedDescription:
        """Optimize a system prompt.

        Args:
            system_prompt: Original system prompt

        Returns:
            Optimization result
        """
        if not self.config.optimize_system_prompts:
            return OptimizedDescription(
                original=system_prompt,
                optimized=system_prompt,
                original_tokens=self._count_tokens(system_prompt),
                optimized_tokens=self._count_tokens(system_prompt),
                token_savings=0,
                token_savings_percent=0.0,
            )

        original_tokens = self._count_tokens(system_prompt)
        optimized = system_prompt
        rules_applied = []

        # Remove personality content
        if self.config.remove_personality_content:
            optimized, applied = self._remove_personality(optimized)
            if applied:
                rules_applied.append("remove_personality")

        # Remove thinking instructions
        if self.config.remove_thinking_instructions:
            optimized, applied = self._remove_thinking_instructions(optimized)
            if applied:
                rules_applied.append("remove_thinking_instructions")

        # Remove redundant sections
        optimized, applied = self._remove_redundant_sections(optimized)
        if applied:
            rules_applied.append("remove_redundant_sections")

        # Simplify structure
        optimized = self._simplify_structure(optimized)
        rules_applied.append("simplify_structure")

        # Clean up formatting
        optimized = self._clean_formatting(optimized)

        optimized_tokens = self._count_tokens(optimized)
        token_savings = original_tokens - optimized_tokens
        token_savings_percent = (token_savings / original_tokens * 100) if original_tokens > 0 else 0.0

        # Update metrics
        self.metrics.total_descriptions_optimized += 1
        self.metrics.total_original_tokens += original_tokens
        self.metrics.total_optimized_tokens += optimized_tokens
        self.metrics.total_token_savings += token_savings
        self.metrics.average_savings_percent = (
            (self.metrics.total_token_savings / self.metrics.total_original_tokens * 100)
            if self.metrics.total_original_tokens > 0
            else 0.0
        )

        return OptimizedDescription(
            original=system_prompt,
            optimized=optimized,
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            token_savings=token_savings,
            token_savings_percent=token_savings_percent,
            rules_applied=rules_applied,
        )

    def _remove_personality(self, text: str) -> tuple[str, bool]:
        """Remove personality and tone instructions.

        Args:
            text: Input text

        Returns:
            Tuple of (optimized text, whether changes were made)
        """
        original = text
        patterns = [
            r"You are (?:a )?(?:helpful|friendly|professional|polite)[^.]+\.",
            r"(?:Always )?(?:be|stay|remain) (?:helpful|friendly|professional|polite)[^.]+\.",
            r"Your tone should be[^.]+\.",
            r"Speak in a[^.]+manner\.",
            r"You should (?:always )?try your best[^.]+\.",
        ]

        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        return text, text != original

    def _remove_thinking_instructions(self, text: str) -> tuple[str, bool]:
        """Remove instructions about how to think.

        Args:
            text: Input text

        Returns:
            Tuple of (optimized text, whether changes were made)
        """
        original = text
        patterns = [
            r"When you (?:encounter|see|find)[^.]+you should[^.]+\.",
            r"(?:First|Then|Next|Finally),?\s+(?:you should|try to|make sure to)[^.]+\.",
            r"If you (?:don't understand|can't|cannot)[^.]+ask[^.]+\.",
            r"Think (?:carefully|step by step|through)[^.]+\.",
            r"Consider[^.]+before[^.]+\.",
            r"Make sure to[^.]+\.",
        ]

        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        return text, text != original

    def _remove_redundant_sections(self, text: str) -> tuple[str, bool]:
        """Remove redundant or verbose sections.

        Args:
            text: Input text

        Returns:
            Tuple of (optimized text, whether changes were made)
        """
        original = text

        # Remove verbose capability listings if tools are already defined
        if "available tools:" in text.lower() and "tools:" in text.lower():
            # Keep only the structured tools section
            text = re.sub(
                r"(?:You have access to|Available to you are)[^:]+:\s*(?:-[^\n]+\n)+",
                "",
                text,
                flags=re.IGNORECASE
            )

        # Remove example sections (LLM doesn't need examples)
        text = re.sub(
            r"(?:For example|Example):\s*(?:[^\n]+\n)+",
            "",
            text,
            flags=re.IGNORECASE
        )

        return text, text != original

    def _simplify_structure(self, text: str) -> str:
        """Simplify prompt structure to bullet points.

        Args:
            text: Input text

        Returns:
            Simplified text
        """
        # Convert paragraphs to bullet points where appropriate
        lines = text.split('\n')
        simplified = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Keep headers and existing bullets
            if line.startswith('#') or line.startswith('-') or line.startswith('*'):
                simplified.append(line)
            # Convert sentences to bullets if they're instructions
            elif any(line.lower().startswith(word) for word in ['you', 'use', 'when', 'if', 'always', 'never']):
                if not line.startswith('- '):
                    simplified.append(f"- {line}")
                else:
                    simplified.append(line)
            else:
                simplified.append(line)

        return '\n'.join(simplified)

    def _clean_formatting(self, text: str) -> str:
        """Clean up formatting and whitespace.

        Args:
            text: Input text

        Returns:
            Cleaned text
        """
        # Remove multiple blank lines
        text = re.sub(r'\n\n\n+', '\n\n', text)

        # Remove trailing whitespace
        lines = [line.rstrip() for line in text.split('\n')]
        text = '\n'.join(lines)

        # Remove leading/trailing whitespace
        text = text.strip()

        return text

    def create_optimized_template(
        self,
        role: str,
        capabilities: list[str],
        constraints: list[str],
        context: str = "",
    ) -> SystemPromptTemplate:
        """Create an optimized system prompt template.

        Args:
            role: One-line role definition
            capabilities: List of capabilities (bullet points)
            constraints: List of constraints (bullet points)
            context: Optional minimal context

        Returns:
            System prompt template
        """
        parts = [f"Role: {role}"]

        if capabilities:
            parts.append("Capabilities:")
            parts.extend([f"- {cap}" for cap in capabilities])

        if constraints:
            parts.append("Constraints:")
            parts.extend([f"- {const}" for const in constraints])

        if context:
            parts.append(f"Context: {context}")

        template = "\n".join(parts)

        return SystemPromptTemplate(
            name="optimized_system_prompt",
            template=template,
            estimated_tokens=self._count_tokens(template),
            description="Optimized system prompt with minimal token usage",
        )

    def _count_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Uses simple word-based estimation: ~0.75 words per token.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        words = len(text.split())
        return int(words / 0.75)

    def get_metrics(self) -> OptimizationMetrics:
        """Get optimization metrics.

        Returns:
            Current metrics
        """
        return self.metrics

    def reset_metrics(self) -> None:
        """Reset metrics to zero."""
        self.metrics = OptimizationMetrics()

    def get_optimization_report(self) -> dict[str, Any]:
        """Get detailed optimization report.

        Returns:
            Report with metrics and statistics
        """
        return {
            "total_optimized": self.metrics.total_descriptions_optimized,
            "original_tokens": self.metrics.total_original_tokens,
            "optimized_tokens": self.metrics.total_optimized_tokens,
            "token_savings": self.metrics.total_token_savings,
            "savings_percent": self.metrics.average_savings_percent,
            "average_original_tokens": (
                self.metrics.total_original_tokens / self.metrics.total_descriptions_optimized
                if self.metrics.total_descriptions_optimized > 0
                else 0
            ),
            "average_optimized_tokens": (
                self.metrics.total_optimized_tokens / self.metrics.total_descriptions_optimized
                if self.metrics.total_descriptions_optimized > 0
                else 0
            ),
        }
