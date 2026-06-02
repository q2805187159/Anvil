import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import React from "react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

vi.mock("react-resizable-panels", () => {
  type MockPanelProps = React.PropsWithChildren<{
    className?: string;
  }>;

  const Panel = React.forwardRef<
    {
      collapse(): void;
      expand(): void;
      getId(): string;
      getSize(): number;
      isCollapsed(): boolean;
      isExpanded(): boolean;
      resize(): void;
    },
    MockPanelProps
  >(function MockPanel({ children, className }, ref) {
    const collapsedRef = React.useRef(false);
    React.useImperativeHandle(ref, () => ({
      collapse() {
        collapsedRef.current = true;
      },
      expand() {
        collapsedRef.current = false;
      },
      getId() {
        return "mock-panel";
      },
      getSize() {
        return collapsedRef.current ? 0 : 100;
      },
      isCollapsed() {
        return collapsedRef.current;
      },
      isExpanded() {
        return !collapsedRef.current;
      },
      resize() {},
    }));
    return React.createElement("div", { className }, children as React.ReactNode);
  });

  const PanelGroup = ({
    children,
    className,
  }: React.PropsWithChildren<{
    className?: string;
  }>) => React.createElement("div", { className }, children as React.ReactNode);

  const PanelResizeHandle = ({ className }: { className?: string }) =>
    React.createElement("div", { className });

  return { Panel, PanelGroup, PanelResizeHandle };
});

if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}
