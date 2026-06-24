"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getBasicConfig, getConfigOverview, saveBasicConfig, testBasicConfig } from "./api";

export function useConfigOverview() {
  return useQuery({
    queryKey: ["config-overview"],
    queryFn: getConfigOverview,
    staleTime: 15000,
  });
}

export function useBasicConfig() {
  return useQuery({
    queryKey: ["config-basics"],
    queryFn: getBasicConfig,
    staleTime: 15000,
  });
}

export function useSaveBasicConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveBasicConfig,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["config-basics"] });
      void queryClient.invalidateQueries({ queryKey: ["config-overview"] });
    },
  });
}

export function useTestBasicConfig() {
  return useMutation({
    mutationFn: testBasicConfig,
  });
}
