"use client";

import * as React from "react";

import { cn } from "@/src/lib/utils";

type TabsContextValue = {
  value: string;
  onValueChange(value: string): void;
};

const TabsContext = React.createContext<TabsContextValue | null>(null);

export function Tabs({
  value,
  onValueChange,
  className,
  children,
}: React.PropsWithChildren<{
  value: string;
  onValueChange(value: string): void;
  className?: string;
}>) {
  return (
    <TabsContext.Provider value={{ value, onValueChange }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabsList({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      aria-orientation="horizontal"
      role="tablist"
      className={cn(
        "inline-flex h-11 items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1 shadow-[var(--shadow-card)]",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({
  className,
  value,
  children,
  ...props
}: React.ComponentProps<"button"> & { value: string }) {
  const context = React.useContext(TabsContext);
  if (!context) {
    throw new Error("TabsTrigger must be used within Tabs");
  }
  const active = context.value === value;

  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={cn(
        "inline-flex h-9 items-center justify-center rounded-[0.7rem] px-3 text-sm font-medium text-[var(--muted)] transition-[background,color,transform] duration-200 ease-[var(--ease-smooth)] active:translate-y-px",
        active && "bg-[var(--accent-soft)] text-[var(--ink)]",
        className,
      )}
      onClick={() => context.onValueChange(value)}
      {...props}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  className,
  value,
  children,
  ...props
}: React.ComponentProps<"div"> & { value: string }) {
  const context = React.useContext(TabsContext);
  if (!context) {
    throw new Error("TabsContent must be used within Tabs");
  }
  const active = context.value === value;

  return (
    <div
      role="tabpanel"
      hidden={!active}
      aria-labelledby={`tab-${value}`}
      className={cn("outline-none", className)}
      {...props}
    >
      {active ? children : null}
    </div>
  );
}
