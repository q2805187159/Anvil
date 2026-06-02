"use client";

import React from "react";

import type { OpsCopy, OpsSurface } from "./types";
import { Button } from "@/src/components/ui/button";
import { cn } from "@/src/lib/utils";

type OpsSurfaceNavProps = {
  copy: OpsCopy;
  activeSurface: OpsSurface;
  onSelect(surface: OpsSurface): void;
};

export function OpsSurfaceNav({ copy, activeSurface, onSelect }: OpsSurfaceNavProps) {
  const surfaces = Object.entries(copy.surfaces) as Array<[OpsSurface, string]>;

  return (
    <nav className="flex h-full flex-col gap-2 border-r border-[var(--line)] bg-[var(--sidebar)] p-3">
      {surfaces.map(([surface, label]) => (
        <Button
          key={surface}
          type="button"
          variant={surface === activeSurface ? "primary" : "ghost"}
          className={cn("justify-start", surface === activeSurface ? "" : "text-[var(--ink)]")}
          onClick={() => onSelect(surface)}
        >
          {label}
        </Button>
      ))}
    </nav>
  );
}
