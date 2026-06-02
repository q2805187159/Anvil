"use client";

import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { useSystemEvents } from "@/src/core/system/hooks";


function SystemEventBridge() {
  useSystemEvents();
  return null;
}

export function QueryProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            staleTime: 5_000,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <SystemEventBridge />
      {children}
    </QueryClientProvider>
  );
}
