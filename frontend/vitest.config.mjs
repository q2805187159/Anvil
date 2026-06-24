import { defineConfig } from "vitest/config";
import path from "node:path";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

function typescriptTransformPlugin() {
  return {
    name: "anvil-vitest-typescript-transform",
    enforce: "pre",
    transform(code, id) {
      const filename = id.split("?")[0];
      if (!filename || filename.includes("/node_modules/") || filename.includes("\\node_modules\\")) {
        return null;
      }
      if (!/\.[cm]?tsx?$/.test(filename)) {
        return null;
      }

      const result = ts.transpileModule(code, {
        fileName: filename,
        compilerOptions: {
          esModuleInterop: true,
          importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
          inlineSources: true,
          jsx: ts.JsxEmit.ReactJSX,
          module: ts.ModuleKind.ESNext,
          sourceMap: true,
          target: ts.ScriptTarget.ES2022,
          useDefineForClassFields: true,
          verbatimModuleSyntax: false,
        },
      });

      return {
        code: result.outputText,
        map: result.sourceMapText ? JSON.parse(result.sourceMapText) : null,
      };
    },
  };
}

export default defineConfig({
  esbuild: false,
  optimizeDeps: {
    noDiscovery: true,
  },
  plugins: [typescriptTransformPlugin()],
  resolve: {
    alias: {
      "@": rootDir,
    },
    preserveSymlinks: true,
  },
  server: {
    host: "127.0.0.1",
  },
  test: {
    pool: "threads",
    deps: {
      optimizer: {
        ssr: {
          enabled: false,
        },
        web: {
          enabled: false,
        },
      },
    },
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
});
