from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryExtractionDecision:
    accepted: bool
    reason: str
    blockers: tuple[str, ...] = ()


_CATEGORY_PREFIX_RE = re.compile(
    r"^(resolved outcome|outcome|project constraint|workflow|environment fact|workspace fact|user preference|correction)\s*:\s*",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s*|\n+")

_EXPLICIT_PREFERENCE_RE = re.compile(
    r"\b(i|we|the user|user)\s+(prefer|prefers|usually|always|never|dislike|dislikes|hate|hates)\b"
    r"|\b(prefer|prefers|dislike|dislikes|avoid)\b\s+[a-z0-9_-]"
    r"|\b(my|our|user's)\s+preferred\b"
    r"|\b(from now on|going forward|by default|default to|always|never|please always|remember that)\b"
    r"|\b(do not|don't|avoid)\b.{0,80}\b(again|in future|going forward|by default)\b"
    r"|以后|今后|总是|每次|默认|长期|记住|偏好|我喜欢|我不喜欢|不要再|以后请",
    re.IGNORECASE,
)
_PREFERENCE_FALSE_POSITIVE_RE = re.compile(
    r"\b(reply|respond|answer)\s+with\s+exactly\b"
    r"|\b(reply|respond|answer)\s+(only|just)\b"
    r"|\bdo\s+not\s+use\s+tools\b"
    r"|\b(no|without)\s+tools\b"
    r"|\btell\s+me\b|\bshow\s+me\b|\bcan\s+you\b|\bplease\s+(create|write|run|search|count|read|open|calculate)\b"
    r"|只回复|只回答|回复\s*OK|不要使用工具|不用工具|告诉我|帮我|创建|运行|搜索|统计|读取|打开|计算",
    re.IGNORECASE,
)
_DURABILITY_MARKER_RE = re.compile(
    r"\b(always|never|default|going forward|from now on|future|recurring|repeated|long[- ]term|stable)\b"
    r"|以后|今后|总是|每次|默认|长期|稳定|反复|重复",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(r"\b(correction|actually|not that|wrong)\b|纠正|其实|不是|错了", re.IGNORECASE)
_TASK_OR_SESSION_NOISE_RE = re.compile(
    r"\b(current session|current turn|this turn|this task|one[- ]off|temporary|scratch|screenshot|uploaded image)\b"
    r"|\b(created file|file created|ran command|command ran|edited file|syntax error|grammar issue|indentation issue)\b"
    r"|\bthread-[a-z0-9_-]+\b|\brun-[a-z0-9_-]+\b|\bcalculator\.py\b"
    r"|当前会话|当前线程|本轮|这个任务|一次性|临时|草稿|截图|已创建文件|文件创建成功|已运行命令|运行了命令|已编辑文件|语法错误|缩进问题",
    re.IGNORECASE,
)
_WORKSPACE_STABLE_RE = re.compile(
    r"\b(project constraint|repo rule|workspace rule|environment variable|deployment rule|workflow|root cause)\b"
    r"|\b(harness-first|middleware|docker|port|api contract|schema|migration|permission|approval|security)\b"
    r"|\b(ci|pytest|test runner|typecheck|lint|build command|regression|verified|tests? passed|returns? 200)\b"
    r"|项目约束|仓库规则|工作区规则|环境变量|部署规则|工作流|根因|回归|测试通过|验证通过|配置|接口|架构|迁移|权限|审批|安全|规则|约束",
    re.IGNORECASE,
)
_EXPLICIT_WORKSPACE_MEMORY_RE = re.compile(
    r"\b(remember|note|record)\b.{0,80}\b(project|repo|repository|workspace|codename|deployment|environment|workflow|rule|constraint)\b"
    r"|\b(project|repo|repository|workspace)\b.{0,80}\b(codename|deployment|environment|workflow|rule|constraint)\b"
    r"|记住.{0,80}(项目|仓库|工作区|代号|部署|环境|工作流|规则|约束)",
    re.IGNORECASE,
)
_DURABLE_OUTCOME_RE = re.compile(
    r"\b(test(?:s)? passed|verified|regression|root cause|deployed|docker|migration|config|api|schema|release|rollback|security|permission|approval|returns? 200)\b"
    r"|测试通过|验证通过|根因|回归|部署|Docker|配置|接口|架构|迁移|权限|审批|安全|规则|约束",
    re.IGNORECASE,
)


def strip_memory_prefix(content: str) -> str:
    return _CATEGORY_PREFIX_RE.sub("", str(content or "").strip()).strip()


def semantic_memory_key(content: str) -> str:
    text = strip_memory_prefix(content)
    text = re.sub(r"\s+", " ", text).lower()
    text = re.sub(r"[`\"'“”‘’。，、；;：:！!？?（）()\[\]{}<>]", "", text)
    return text.strip()


def is_task_or_session_noise(content: str) -> bool:
    return bool(_TASK_OR_SESSION_NOISE_RE.search(strip_memory_prefix(content)))


def has_durable_outcome_signal(content: str, *, signals: dict[str, Any] | None = None) -> bool:
    if _DURABLE_OUTCOME_RE.search(content):
        return True
    if not isinstance(signals, dict):
        return False
    durable_keys = {
        "tests_passed",
        "verification_passed",
        "deployed",
        "docker_updated",
        "root_cause",
        "user_correction",
        "project_constraint",
    }
    return any(bool(signals.get(key)) for key in durable_keys)


def is_durable_user_preference(content: str) -> bool:
    text = strip_memory_prefix(content)
    if not _EXPLICIT_PREFERENCE_RE.search(text):
        return False
    if _PREFERENCE_FALSE_POSITIVE_RE.search(text) and not _DURABILITY_MARKER_RE.search(text):
        return False
    if is_task_or_session_noise(text) and not _DURABILITY_MARKER_RE.search(text):
        return False
    return True


def is_stable_workspace_memory(content: str, *, signals: dict[str, Any] | None = None) -> bool:
    text = strip_memory_prefix(content)
    if _WORKSPACE_STABLE_RE.search(text):
        return True
    if _EXPLICIT_WORKSPACE_MEMORY_RE.search(text):
        return True
    return has_durable_outcome_signal(text, signals=signals)


def durable_preference_sentences(text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(str(text or "")):
        sentence = raw.strip()
        if sentence and is_durable_user_preference(sentence):
            matches.append(sentence)
    return tuple(dict.fromkeys(matches))


def memory_extraction_decision(
    *,
    content: str,
    category: str,
    layer_id: str,
    confidence: float,
    evidence_refs: tuple[str, ...] = (),
    supersedes: tuple[str, ...] = (),
    signals: dict[str, Any] | None = None,
) -> MemoryExtractionDecision:
    text = strip_memory_prefix(content)
    key = semantic_memory_key(text)
    if not key:
        return MemoryExtractionDecision(False, "empty_content", ("empty_content",))
    normalized_category = str(category or "").strip().lower()
    normalized_layer = str(layer_id or "").strip().lower()
    if len(key) < 24 and normalized_category not in {"preference", "correction"}:
        return MemoryExtractionDecision(False, "too_short", ("too_short",))
    if _TASK_OR_SESSION_NOISE_RE.search(text) and normalized_category not in {"preference", "correction", "project_constraint"}:
        return MemoryExtractionDecision(False, "task_or_session_noise", ("task_or_session_noise",))
    if normalized_layer == "user" and normalized_category == "preference":
        if not is_durable_user_preference(text):
            return MemoryExtractionDecision(False, "non_durable_user_preference", ("non_durable_user_preference",))
    if normalized_layer == "user" and normalized_category == "correction":
        if _PREFERENCE_FALSE_POSITIVE_RE.search(text) and not _DURABILITY_MARKER_RE.search(text):
            return MemoryExtractionDecision(False, "one_off_user_correction", ("one_off_user_correction",))
    if normalized_category in {"resolved_outcome", "outcome"}:
        if not has_durable_outcome_signal(text, signals=signals) and not supersedes:
            return MemoryExtractionDecision(False, "missing_durable_outcome_signal", ("missing_durable_outcome_signal",))
    if normalized_layer == "workspace" and normalized_category in {"project_context", "environment", "workflow", "project_constraint"}:
        if confidence < 0.82 and not is_stable_workspace_memory(text, signals=signals):
            return MemoryExtractionDecision(False, "weak_workspace_signal", ("weak_workspace_signal",))
    if normalized_category == "goal" and not (_DURABILITY_MARKER_RE.search(text) or evidence_refs):
        return MemoryExtractionDecision(False, "one_off_goal", ("one_off_goal",))
    return MemoryExtractionDecision(True, "accepted", ())


def correction_or_preference_signal(content: str) -> bool:
    return bool(_CORRECTION_RE.search(content) or is_durable_user_preference(content))
