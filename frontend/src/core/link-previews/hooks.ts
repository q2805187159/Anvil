"use client";

import { useQuery } from "@tanstack/react-query";

import { getLinkPreview } from "./api";

export function useLinkPreview(url: string | null) {
  return useQuery({
    queryKey: ["link-preview", url],
    queryFn: () => getLinkPreview(url!),
    enabled: Boolean(url),
    staleTime: 60_000,
  });
}
