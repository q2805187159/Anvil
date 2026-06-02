"""Pattern-based summarizer using learned patterns."""

from __future__ import annotations

import re
from datetime import datetime

from .contracts import SummarizationResult, TokenOptimizationConfig


class PatternSummarizer:
    """Summarizes content using learned patterns and templates.

    Uses pattern matching to identify content structure and
    generate concise summaries at multiple levels.
    """

    def __init__(self, config: TokenOptimizationConfig):
        """Initialize summarizer.

        Args:
            config: Token optimization configuration
        """
        self.config = config
        self._templates = self._build_templates()

    def _build_templates(self) -> dict[str, dict[str, str]]:
        """Build summarization templates.

        Returns:
            Templates by pattern type and level
        """
        return {
            "action_sequence": {
                "detailed": "{action} {target} with {details}",
                "brief": "{action} {target}",
                "ultra-brief": "{action}",
            },
            "result_description": {
                "detailed": "Result: {outcome} with {metrics}",
                "brief": "Result: {outcome}",
                "ultra-brief": "{outcome}",
            },
            "error_report": {
                "detailed": "Error: {error_type} in {location} - {message}",
                "brief": "Error: {error_type} - {message}",
                "ultra-brief": "Error: {error_type}",
            },
            "file_operation": {
                "detailed": "{operation} {file_path} ({size} bytes, {lines} lines)",
                "brief": "{operation} {file_path}",
                "ultra-brief": "{operation}",
            },
        }

    def summarize(
        self,
        content: str,
        level: str = "brief",
        patterns: list[dict] | None = None,
    ) -> SummarizationResult:
        """Summarize content at specified level.

        Args:
            content: Content to summarize
            level: Summarization level (detailed, brief, ultra-brief)
            patterns: Optional learned patterns to use

        Returns:
            Summarization result
        """
        if not self.config.enable_pattern_summarization:
            tokens = self._count_tokens(content)
            return SummarizationResult(
                original=content,
                summary=content,
                original_tokens=tokens,
                summary_tokens=tokens,
                token_savings=0,
                summarization_level=level,
            )

        start_time = datetime.now()

        # Detect pattern type
        pattern_type = self._detect_pattern(content)

        # Generate summary based on pattern and level
        summary = self._generate_summary(content, pattern_type, level)

        # Calculate metrics
        original_tokens = self._count_tokens(content)
        summary_tokens = self._count_tokens(summary)
        token_savings = original_tokens - summary_tokens

        summarization_time = (datetime.now() - start_time).total_seconds() * 1000

        return SummarizationResult(
            original=content,
            summary=summary,
            original_tokens=original_tokens,
            summary_tokens=summary_tokens,
            token_savings=token_savings,
            summarization_level=level,
            pattern_used=pattern_type,
            summarization_time_ms=summarization_time,
        )

    def _detect_pattern(self, content: str) -> str:
        """Detect content pattern type.

        Args:
            content: Content to analyze

        Returns:
            Pattern type
        """
        content_lower = content.lower()

        # Check for error patterns
        if any(word in content_lower for word in ['error', 'exception', 'failed', 'failure']):
            return "error_report"

        # Check for file operation patterns
        if any(word in content_lower for word in ['read', 'write', 'edit', 'delete', 'file']):
            return "file_operation"

        # Check for result patterns
        if any(word in content_lower for word in ['result', 'output', 'returned', 'completed']):
            return "result_description"

        # Default to action sequence
        return "action_sequence"

    def _generate_summary(self, content: str, pattern_type: str, level: str) -> str:
        """Generate summary using pattern and level.

        Args:
            content: Content to summarize
            pattern_type: Detected pattern type
            level: Summarization level

        Returns:
            Generated summary
        """
        # Get template for pattern and level
        template = self._templates.get(pattern_type, {}).get(level)

        if not template:
            # Fallback to simple truncation
            return self._simple_summarize(content, level)

        # Extract information based on pattern
        info = self._extract_info(content, pattern_type)

        # Fill template
        try:
            summary = template.format(**info)
        except KeyError:
            # Fallback if template variables not found
            summary = self._simple_summarize(content, level)

        return summary

    def _extract_info(self, content: str, pattern_type: str) -> dict[str, str]:
        """Extract information from content based on pattern.

        Args:
            content: Content to extract from
            pattern_type: Pattern type

        Returns:
            Extracted information
        """
        info = {}

        if pattern_type == "action_sequence":
            # Extract action verb
            words = content.split()
            if words:
                info["action"] = words[0]
                info["target"] = " ".join(words[1:3]) if len(words) > 1 else ""
                info["details"] = " ".join(words[3:]) if len(words) > 3 else ""

        elif pattern_type == "error_report":
            # Extract error information
            error_match = re.search(r'(error|exception|failed):\s*(\w+)', content, re.IGNORECASE)
            if error_match:
                info["error_type"] = error_match.group(2)
            else:
                info["error_type"] = "Unknown"

            info["location"] = self._extract_location(content)
            info["message"] = content[:100]  # First 100 chars

        elif pattern_type == "file_operation":
            # Extract file operation details
            op_match = re.search(r'(read|write|edit|delete|create)', content, re.IGNORECASE)
            if op_match:
                info["operation"] = op_match.group(1)
            else:
                info["operation"] = "operation"

            path_match = re.search(r'([/\\][\w/\\.-]+)', content)
            if path_match:
                info["file_path"] = path_match.group(1)
            else:
                info["file_path"] = "file"

            size_match = re.search(r'(\d+)\s*bytes', content)
            info["size"] = size_match.group(1) if size_match else "unknown"

            lines_match = re.search(r'(\d+)\s*lines', content)
            info["lines"] = lines_match.group(1) if lines_match else "unknown"

        elif pattern_type == "result_description":
            # Extract result information
            outcome_match = re.search(r'(success|completed|finished|done|failed)', content, re.IGNORECASE)
            if outcome_match:
                info["outcome"] = outcome_match.group(1)
            else:
                info["outcome"] = "completed"

            metrics_match = re.search(r'(\d+\s*\w+)', content)
            info["metrics"] = metrics_match.group(1) if metrics_match else ""

        return info

    def _extract_location(self, content: str) -> str:
        """Extract location from content.

        Args:
            content: Content to extract from

        Returns:
            Location string
        """
        # Try to find file path
        path_match = re.search(r'([/\\][\w/\\.-]+)', content)
        if path_match:
            return path_match.group(1)

        # Try to find function name
        func_match = re.search(r'(\w+)\(', content)
        if func_match:
            return func_match.group(1)

        return "unknown"

    def _simple_summarize(self, content: str, level: str) -> str:
        """Simple summarization by truncation.

        Args:
            content: Content to summarize
            level: Summarization level

        Returns:
            Summarized content
        """
        if level == "ultra-brief":
            # First sentence or 50 chars
            first_sentence = content.split('.')[0]
            return first_sentence[:50] + "..." if len(first_sentence) > 50 else first_sentence

        elif level == "brief":
            # First 2 sentences or 150 chars
            sentences = content.split('.')[:2]
            brief = '. '.join(sentences)
            return brief[:150] + "..." if len(brief) > 150 else brief

        else:  # detailed
            # First 3 sentences or 300 chars
            sentences = content.split('.')[:3]
            detailed = '. '.join(sentences)
            return detailed[:300] + "..." if len(detailed) > 300 else detailed

    def _count_tokens(self, text: str) -> int:
        """Estimate token count.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        words = len(text.split())
        return int(words / 0.75)

    def summarize_batch(
        self,
        contents: list[str],
        level: str = "brief",
    ) -> list[SummarizationResult]:
        """Summarize multiple contents.

        Args:
            contents: List of contents to summarize
            level: Summarization level

        Returns:
            List of summarization results
        """
        return [self.summarize(content, level) for content in contents]
