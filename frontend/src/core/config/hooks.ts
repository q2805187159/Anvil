"use client";

import { useQuery } from "@tanstack/react-query";

import { getConfigOverview } from "./api";

export function useConfigOverview() {
  return useQuery({
    queryKey: ["config-overview"],
    queryFn: getConfigOverview,
    staleTime: 15000,
  });
}
