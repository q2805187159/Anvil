import React from "react";
import type { Metadata } from "next";

import "./globals.css";
import { QueryProvider } from "@/src/core/query/provider";
import { ThemeProvider } from "@/src/core/theme/provider";
import { I18nProvider } from "@/src/core/i18n";
import { TooltipProvider } from "@/src/components/ui/tooltip";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Anvil",
  description: "Operator workspace for the Anvil gateway",
  icons: {
    icon: "/favicon.svg",
  },
  other: {
    google: "notranslate",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" translate="no" className="notranslate" suppressHydrationWarning>
      <head>
        <meta name="google" content="notranslate" />
        <meta httpEquiv="Content-Language" content="en-US, zh-CN" />
      </head>
      <body translate="no" className="notranslate">
        <ThemeProvider>
          <I18nProvider>
            <TooltipProvider>
              <QueryProvider>
                {children}
              </QueryProvider>
            </TooltipProvider>
          </I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
