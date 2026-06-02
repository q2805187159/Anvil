"""Benchmark Report Generator

Generates detailed markdown reports from benchmark test results.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .benchmark_framework import TestResult, BenchmarkSuite, TestStatus


class BenchmarkReportGenerator:
    """Generates markdown reports from benchmark results."""

    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_test_report(self, result: TestResult) -> str:
        """Generate detailed report for a single test."""
        status_emoji = {
            TestStatus.SUCCESS: "✅",
            TestStatus.FAILURE: "❌",
            TestStatus.PARTIAL: "⚠️",
            TestStatus.SKIPPED: "⏭️",
        }

        report = f"""# Test Report: {result.test_name}

{status_emoji.get(result.status, "❓")} **Status**: {result.status.value.upper()}

## Category
{result.category.value.replace('_', ' ').title()}

## Description
{result.description}

## Execution Summary
- **Status**: {result.status.value}
- **Duration**: {result.duration_ms:.2f}ms
- **Quality Score**: {result.quality_score:.1f}/10
- **Timestamp**: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}

---

## Metrics

### Token Usage
| Metric | Value |
|--------|-------|
| Input Tokens | {result.metrics.tokens.input_tokens:,} |
| Output Tokens | {result.metrics.tokens.output_tokens:,} |
| Cached Tokens | {result.metrics.tokens.cached_tokens:,} |
| Saved Tokens | {result.metrics.tokens.saved_tokens:,} |
| **Total Tokens** | **{result.metrics.tokens.total_tokens:,}** |
| **Savings** | **{result.metrics.tokens.savings_percentage:.1f}%** |

### Performance
| Metric | Value |
|--------|-------|
| Execution Time | {result.metrics.performance.execution_time_ms:.2f}ms |
| Overhead | {result.metrics.performance.overhead_ms:.2f}ms |
| Cache Hits | {result.metrics.performance.cache_hits} |
| Cache Misses | {result.metrics.performance.cache_misses} |
| **Cache Hit Rate** | **{result.metrics.performance.cache_hit_rate:.1f}%** |

### Optimization Impact
| Component | Tokens Saved |
|-----------|--------------|
| Compaction | {result.metrics.optimization.compaction_saved:,} |
| JIT Loading | {result.metrics.optimization.jit_loading_saved:,} |
| Prompt Optimization | {result.metrics.optimization.prompt_opt_saved:,} |
| Token Optimization | {result.metrics.optimization.token_opt_saved:,} |
| Caching | {result.metrics.optimization.caching_saved:,} |
| **Total Saved** | **{result.metrics.optimization.total_saved:,}** |

---

## Tool Calls
"""

        if result.metrics.tool_calls:
            report += "\n| # | Tool | Purpose | Result | Duration | Status |\n"
            report += "|---|------|---------|--------|----------|--------|\n"
            for i, tc in enumerate(result.metrics.tool_calls, 1):
                status_icon = "✅" if tc.success else "❌"
                report += f"| {i} | {tc.tool_name} | {tc.purpose} | {tc.result} | {tc.duration_ms:.1f}ms | {status_icon} |\n"
        else:
            report += "\nNo tool calls recorded.\n"

        report += "\n---\n\n## Skills Usage\n"

        if result.metrics.skill_usage:
            report += "\n| # | Skill | When Used | Outcome | Status |\n"
            report += "|---|-------|-----------|---------|--------|\n"
            for i, su in enumerate(result.metrics.skill_usage, 1):
                status_icon = "✅" if su.success else "❌"
                report += f"| {i} | {su.skill_name} | {su.when_used} | {su.outcome} | {status_icon} |\n"
        else:
            report += "\nNo skills used.\n"

        report += "\n---\n\n## Memory Operations\n\n"
        report += f"- **Extracted**: {result.metrics.memory.extracted_count} items\n"
        report += f"- **Injected**: {result.metrics.memory.injected_count} items\n"
        report += f"- **Quality Score**: {result.metrics.memory.quality_score:.2f}\n\n"

        if result.metrics.memory.extracted_items:
            report += "### Extracted Items\n"
            for item in result.metrics.memory.extracted_items:
                report += f"- {item}\n"
            report += "\n"

        if result.metrics.memory.injected_items:
            report += "### Injected Items\n"
            for item in result.metrics.memory.injected_items:
                report += f"- {item}\n"
            report += "\n"

        report += "---\n\n"

        if result.issues:
            report += "## Issues Identified\n\n"
            for i, issue in enumerate(result.issues, 1):
                report += f"{i}. {issue}\n"
            report += "\n"

        if result.recommendations:
            report += "## Recommendations\n\n"
            for i, rec in enumerate(result.recommendations, 1):
                report += f"{i}. {rec}\n"
            report += "\n"

        report += "---\n\n"
        report += f"*Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"

        return report

    def generate_suite_report(self, suite: BenchmarkSuite) -> str:
        """Generate aggregate report for entire test suite."""
        summary = suite.get_summary()

        report = f"""# Benchmark Testing Summary: {suite.name}

**Description**: {suite.description}
**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Overall Results

| Metric | Value |
|--------|-------|
| **Total Tests** | {summary['total_tests']} |
| **Passed** | {summary['passed']} ({summary['pass_rate']:.1f}%) |
| **Failed** | {summary['failed']} |
| **Partial** | {summary['partial']} |
| **Average Quality** | {summary['average_quality']:.1f}/10 |
| **Average Duration** | {summary['average_duration_ms']:.2f}ms |

---

## Performance Summary

### Token Optimization
| Metric | Value |
|--------|-------|
| Total Tokens Saved | {summary['metrics']['tokens']['total_saved']:,} |
| Total Tokens Used | {summary['metrics']['tokens']['total_used']:,} |
| **Savings Percentage** | **{summary['metrics']['tokens']['savings_pct']:.1f}%** |

### Caching Performance
| Metric | Value |
|--------|-------|
| Cache Hits | {summary['metrics']['performance']['cache_hits']:,} |
| Cache Misses | {summary['metrics']['performance']['cache_misses']:,} |
| **Cache Hit Rate** | **{summary['metrics']['performance']['cache_hit_rate']:.1f}%** |
| Average Overhead | {summary['metrics']['performance']['avg_overhead_ms']:.2f}ms |

---

## Results by Category

"""

        for category, stats in summary['by_category'].items():
            report += f"### {category.replace('_', ' ').title()}\n"
            report += f"- Tests: {stats['total']}\n"
            report += f"- Passed: {stats['passed']} ({stats['pass_rate']:.1f}%)\n"
            report += f"- Average Quality: {stats['avg_quality']:.1f}/10\n\n"

        report += "---\n\n## Individual Test Results\n\n"
        report += "| # | Test Name | Category | Status | Quality | Duration |\n"
        report += "|---|-----------|----------|--------|---------|----------|\n"

        for i, result in enumerate(suite.results, 1):
            status_emoji = {
                TestStatus.SUCCESS: "✅",
                TestStatus.FAILURE: "❌",
                TestStatus.PARTIAL: "⚠️",
                TestStatus.SKIPPED: "⏭️",
            }
            emoji = status_emoji.get(result.status, "❓")
            category_short = result.category.value.replace('_', ' ').title()
            report += f"| {i} | {result.test_name} | {category_short} | {emoji} {result.status.value} | {result.quality_score:.1f}/10 | {result.duration_ms:.1f}ms |\n"

        report += "\n---\n\n## Quality Assessment\n\n"

        # Calculate quality breakdown
        quality_scores = {
            "Architecture": 0.0,
            "Performance": 0.0,
            "Reliability": 0.0,
            "Efficiency": 0.0,
        }

        # Simple heuristic based on results
        if summary['pass_rate'] >= 90:
            quality_scores["Reliability"] = 9.5
        elif summary['pass_rate'] >= 80:
            quality_scores["Reliability"] = 8.5
        elif summary['pass_rate'] >= 70:
            quality_scores["Reliability"] = 7.5
        else:
            quality_scores["Reliability"] = 6.0

        if summary['metrics']['tokens']['savings_pct'] >= 60:
            quality_scores["Efficiency"] = 9.5
        elif summary['metrics']['tokens']['savings_pct'] >= 50:
            quality_scores["Efficiency"] = 8.5
        else:
            quality_scores["Efficiency"] = 7.5

        if summary['metrics']['performance']['avg_overhead_ms'] < 50:
            quality_scores["Performance"] = 9.5
        elif summary['metrics']['performance']['avg_overhead_ms'] < 100:
            quality_scores["Performance"] = 8.5
        else:
            quality_scores["Performance"] = 7.5

        quality_scores["Architecture"] = summary['average_quality']

        overall_quality = sum(quality_scores.values()) / len(quality_scores)

        for aspect, score in quality_scores.items():
            report += f"- **{aspect}**: {score:.1f}/10\n"

        report += f"\n**Overall Quality**: {overall_quality:.1f}/10\n\n"

        report += "---\n\n## Recommendations\n\n"

        # Generate recommendations based on results
        recommendations = []

        if summary['pass_rate'] < 90:
            recommendations.append(f"Improve test pass rate from {summary['pass_rate']:.1f}% to 90%+")

        if summary['average_quality'] < 8.5:
            recommendations.append(f"Increase average quality score from {summary['average_quality']:.1f} to 8.5+")

        if summary['metrics']['tokens']['savings_pct'] < 60:
            recommendations.append(f"Optimize token savings from {summary['metrics']['tokens']['savings_pct']:.1f}% to 60%+")

        if summary['metrics']['performance']['cache_hit_rate'] < 30:
            recommendations.append(f"Improve cache hit rate from {summary['metrics']['performance']['cache_hit_rate']:.1f}% to 30%+")

        if summary['metrics']['performance']['avg_overhead_ms'] > 50:
            recommendations.append(f"Reduce optimization overhead from {summary['metrics']['performance']['avg_overhead_ms']:.1f}ms to <50ms")

        # Add category-specific recommendations
        for category, stats in summary['by_category'].items():
            if stats['pass_rate'] < 80:
                recommendations.append(f"Focus on {category.replace('_', ' ')} tests (only {stats['pass_rate']:.1f}% passing)")

        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                report += f"{i}. {rec}\n"
        else:
            report += "All targets met! No critical recommendations.\n"

        report += "\n---\n\n"
        report += f"*Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"

        return report

    def save_test_report(self, result: TestResult) -> Path:
        """Generate and save test report to file."""
        report = self.generate_test_report(result)
        filename = f"test_{result.test_name.lower().replace(' ', '_')}.md"
        filepath = self.output_dir / filename

        filepath.write_text(report, encoding='utf-8')
        return filepath

    def save_suite_report(self, suite: BenchmarkSuite) -> Path:
        """Generate and save suite report to file."""
        report = self.generate_suite_report(suite)
        filename = f"suite_{suite.name.lower().replace(' ', '_')}.md"
        filepath = self.output_dir / filename

        filepath.write_text(report, encoding='utf-8')
        return filepath

    def save_all_reports(self, suite: BenchmarkSuite) -> dict[str, Path]:
        """Generate and save all reports."""
        saved_files = {}

        # Save suite summary
        suite_file = self.save_suite_report(suite)
        saved_files['suite_summary'] = suite_file

        # Save individual test reports
        for result in suite.results:
            test_file = self.save_test_report(result)
            saved_files[result.test_name] = test_file

        return saved_files
