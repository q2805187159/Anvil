from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemorySignals:
    correction: bool = False
    reinforcement: bool = False
    error: bool = False
    retry: bool = False
    resolved: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "correction": self.correction,
            "reinforcement": self.reinforcement,
            "error": self.error,
            "retry": self.retry,
            "resolved": self.resolved,
        }


class MemorySignalDetector:
    """Small deterministic signal detector used to steer the structured updater."""

    _CORRECTION_MARKERS = (
        "actually",
        "correction",
        "instead",
        "update ",
        "replace ",
        "remove ",
        "not ",
        "不是",
        "不对",
        "纠正",
        "应该是",
        "更新",
        "替换",
        "移除",
        "删除",
    )
    _REINFORCE_MARKERS = ("remember", "keep using", "继续", "记住", "以后都", "保持")
    _ERROR_MARKERS = ("error", "failed", "traceback", "exception", "报错", "失败", "错误")
    _RETRY_MARKERS = ("retry", "tried again", "rerun", "重试", "再试", "重新运行")
    _RESOLVED_MARKERS = (
        "fixed",
        "resolved",
        "verified",
        "passed",
        "修复",
        "解决",
        "验证通过",
        "已完成",
        "成功",
    )

    def detect(self, *, user_content: str, assistant_content: str, status: str = "completed") -> MemorySignals:
        user = user_content.lower()
        assistant = assistant_content.lower()
        combined = f"{user}\n{assistant}"
        return MemorySignals(
            correction=_has_marker(user, self._CORRECTION_MARKERS) or _has_marker(assistant, ("corrected", "更正")),
            reinforcement=_has_marker(combined, self._REINFORCE_MARKERS),
            error=status.lower() in {"failed", "error", "interrupted"} or _has_marker(combined, self._ERROR_MARKERS),
            retry=_has_marker(combined, self._RETRY_MARKERS),
            resolved=status.lower() == "completed" and _has_marker(assistant, self._RESOLVED_MARKERS),
        )


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
