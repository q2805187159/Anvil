"use client";

import { useQuery } from "@tanstack/react-query";

import { listExtensions } from "./api";

export function useExtensions() {
  return useQuery({
    queryKey: ["extensions"],
    queryFn: listExtensions,
  });
}
