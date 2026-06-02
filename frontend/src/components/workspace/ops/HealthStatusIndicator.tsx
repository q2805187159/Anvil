import React from "react";
import { cn } from "@/src/lib/utils";

interface HealthStatusIndicatorProps {
  status: "operational" | "degraded" | "failed";
  responseTimeMs?: number;
  message?: string;
  className?: string;
  labels?: Partial<Record<HealthStatusIndicatorProps["status"], string>>;
}

const statusConfig = {
  operational: {
    color: "bg-emerald-500",
    label: "Ready",
    textColor: "text-emerald-600 dark:text-emerald-400",
    dotAnimation: "animate-pulse",
  },
  degraded: {
    color: "bg-yellow-500",
    label: "Degraded",
    textColor: "text-yellow-600 dark:text-yellow-400",
    dotAnimation: "",
  },
  failed: {
    color: "bg-red-500",
    label: "Unavailable",
    textColor: "text-red-600 dark:text-red-400",
    dotAnimation: "",
  },
};

export const HealthStatusIndicator: React.FC<HealthStatusIndicatorProps> = ({
  status,
  responseTimeMs,
  message,
  className,
  labels,
}) => {
  const config = statusConfig[status];
  const label = labels?.[status] ?? config.label;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex items-center gap-2">
        <div className={cn("w-2 h-2 rounded-full", config.color, config.dotAnimation)} />
        <span className={cn("text-xs font-medium", config.textColor)}>
          {label}
          {responseTimeMs !== undefined && ` (${responseTimeMs}ms)`}
        </span>
      </div>
      {message && (
        <p className="text-xs text-[var(--muted)] ml-4 truncate" title={message}>
          {message}
        </p>
      )}
    </div>
  );
};
