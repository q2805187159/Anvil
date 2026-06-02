/**
 * Unified Design System Components
 *
 * This module exports all reusable UI components for the Anvil frontend.
 * These components provide consistent styling, behavior, and accessibility
 * across the entire application.
 *
 * Usage:
 * ```tsx
 * import { Select, FormField, SectionCard } from "@/src/components/ui";
 * ```
 */

// Form Components
export { Select, FormField } from "./select";
export type { SelectOption, SelectProps, FormFieldProps } from "./select";

// Layout Components
export {
  SectionCard,
  InfoRow,
  EmptyPanelText,
  PanelContainer,
  PanelHeader,
  DataCard,
  Metric,
  Divider,
  StatusIndicator,
} from "./layout";
export type {
  SectionCardProps,
  InfoRowProps,
  EmptyPanelTextProps,
  PanelContainerProps,
  PanelHeaderProps,
  DataCardProps,
  MetricProps,
  DividerProps,
  StatusIndicatorProps,
} from "./layout";

// Base UI Components (existing)
export { Badge } from "./badge";
export { Button } from "./button";
export { Card, CardHeader, CardTitle, CardContent } from "./card";
export { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./dialog";
export { Input } from "./input";
export { NativeSelect } from "./native-select";
export { ScrollArea } from "./scroll-area";
export { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";
export { Textarea } from "./textarea";
export { Tooltip, TooltipProvider } from "./tooltip";
