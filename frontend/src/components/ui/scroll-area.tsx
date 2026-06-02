"use client";

import * as React from "react";

import { cn } from "@/src/lib/utils";

export function ScrollArea({
  className,
  children,
  viewportRef,
  hideHorizontalScrollbar = false,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  viewportRef?: React.Ref<HTMLDivElement>;
  hideHorizontalScrollbar?: boolean;
}) {
  return (
    <div className={cn("relative min-h-0 overflow-hidden", className)} {...props}>
      <div
        ref={viewportRef}
        className={cn(
          "size-full min-h-0 rounded-[inherit] overflow-y-auto",
          hideHorizontalScrollbar ? "overflow-x-hidden" : "overflow-x-auto",
        )}
      >
        {children}
      </div>
    </div>
  );
}
