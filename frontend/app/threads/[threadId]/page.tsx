import React from "react";

import { WorkspaceShell } from "@/src/components/workspace/workspace-shell";

export default async function ThreadPage({
  params,
}: Readonly<{
  params: Promise<{ threadId: string }>;
}>) {
  const { threadId } = await params;
  return <WorkspaceShell initialThreadId={decodeURIComponent(threadId)} />;
}
