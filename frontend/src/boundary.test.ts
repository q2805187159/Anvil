import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";


function collectFiles(root: string): string[] {
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(fullPath));
    } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name)) {
      files.push(fullPath);
    }
  }
  return files;
}


describe("frontend boundaries", () => {
  it("does not import harness or backend runtime modules", () => {
    const roots = [path.resolve(__dirname), path.resolve(__dirname, "../app")];
    const forbidden = [
      "backend/packages/harness",
      "backend/app",
      "app.gateway",
      "app.sdk",
    ];

    for (const root of roots) {
      for (const file of collectFiles(root)) {
        if (file.endsWith("boundary.test.ts")) {
          continue;
        }
        const source = fs.readFileSync(file, "utf-8");
        for (const token of forbidden) {
          expect(source.includes(token), `${file} imports forbidden token ${token}`).toBe(false);
        }
      }
    }
  });
});
