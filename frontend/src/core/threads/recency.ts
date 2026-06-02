import type { ThreadView } from "@/src/core/contracts";
import type { Locale } from "@/src/core/i18n";

export function threadActivityAt(thread: Pick<ThreadView, "updated_at" | "last_message_at">): string | null {
  return thread.last_message_at || thread.updated_at || null;
}

export function threadActivityAtMillis(thread: Pick<ThreadView, "updated_at" | "last_message_at">): number {
  const activityAt = threadActivityAt(thread);
  const parsed = activityAt ? Date.parse(activityAt) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

export function sortThreadsByRecency(threads: ThreadView[]): ThreadView[] {
  return [...threads].sort((left, right) => {
    const recencyDiff = threadActivityAtMillis(right) - threadActivityAtMillis(left);
    if (recencyDiff !== 0) {
      return recencyDiff;
    }
    return left.thread_id.localeCompare(right.thread_id);
  });
}

export function formatThreadActivityAge(activityAt: string | null | undefined, locale: Locale, nowMs = Date.now()): string {
  if (!activityAt) {
    return "";
  }
  const updatedMs = Date.parse(activityAt);
  if (!Number.isFinite(updatedMs)) {
    return "";
  }
  const elapsedSeconds = Math.max(Math.floor((nowMs - updatedMs) / 1000), 0);
  if (elapsedSeconds < 60) {
    return locale === "zh-CN" ? "刚刚" : "now";
  }
  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) {
    return locale === "zh-CN" ? `${elapsedMinutes} 分` : `${elapsedMinutes}m`;
  }
  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) {
    return locale === "zh-CN" ? `${elapsedHours} 小时` : `${elapsedHours}h`;
  }
  const elapsedDays = Math.floor(elapsedHours / 24);
  if (elapsedDays < 7) {
    return locale === "zh-CN" ? `${elapsedDays} 天` : `${elapsedDays}d`;
  }
  const elapsedWeeks = Math.floor(elapsedDays / 7);
  if (elapsedWeeks < 5) {
    return locale === "zh-CN" ? `${elapsedWeeks} 周` : `${elapsedWeeks}w`;
  }
  const elapsedMonths = Math.floor(elapsedDays / 30);
  if (elapsedMonths < 12) {
    return locale === "zh-CN" ? `${Math.max(elapsedMonths, 1)} 个月` : `${Math.max(elapsedMonths, 1)}mo`;
  }
  const elapsedYears = Math.floor(elapsedDays / 365);
  return locale === "zh-CN" ? `${Math.max(elapsedYears, 1)} 年` : `${Math.max(elapsedYears, 1)}y`;
}

export const formatThreadUpdatedAge = formatThreadActivityAge;
