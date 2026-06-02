"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { getGatewayHealth, streamSystemEvents } from "./api";

export function useGatewayHealth() {
  return useQuery({
    queryKey: ["gateway-health"],
    queryFn: getGatewayHealth,
    refetchInterval: 15000,
  });
}

export function useSystemEvents() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const controller = new AbortController();
    let meaningfulEventSeen = false;
    let retries = 0;

    const connect = async () => {
      try {
        for await (const event of streamSystemEvents(controller.signal)) {
          meaningfulEventSeen = true;
          const systemVersion = typeof event.system_version === "number"
            ? event.system_version
            : typeof event.data.system_version === "number"
              ? event.data.system_version
              : 0;
          queryClient.setQueryData(["system-version"], systemVersion);
          if (event.event === "skills_changed") {
            void queryClient.invalidateQueries({ queryKey: ["skills"] });
            void queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
            void queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
            void queryClient.invalidateQueries({ queryKey: ["threads"] });
          }
          if (event.event === "capabilities_changed" || event.event === "config_reloaded") {
            void queryClient.invalidateQueries({ queryKey: ["models"] });
            void queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
            void queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
            void queryClient.invalidateQueries({ queryKey: ["extensions"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-server-tools"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-server-provenance"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-resources"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-prompts"] });
            void queryClient.invalidateQueries({ queryKey: ["plugins"] });
            void queryClient.invalidateQueries({ queryKey: ["threads"] });
          }
          if (event.event === "mcp_server_status") {
            void queryClient.invalidateQueries({ queryKey: ["extensions"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-server-tools"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-server-provenance"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-resources"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-prompts"] });
            void queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
            void queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
          }
          if (event.event === "reload_failed") {
            void queryClient.invalidateQueries({ queryKey: ["extensions"] });
            void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
          }
        }
      } catch (error) {
        if (controller.signal.aborted || meaningfulEventSeen || retries >= 2) {
          return;
        }
        retries += 1;
        window.setTimeout(() => {
          void connect();
        }, 500 * retries);
      }
    };

    void connect();
    return () => controller.abort();
  }, [queryClient]);
}
