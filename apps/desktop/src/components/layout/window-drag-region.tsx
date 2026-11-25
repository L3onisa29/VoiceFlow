import { cn } from "cn";

/** Top strip of the overlay title bar: drags the window, holds the traffic lights. */
export function WindowDragRegion({ className }: { className?: string }) {
  return (
    <div
      data-tauri-drag-region
      aria-hidden="true"
      className={cn("h-9.5 shrink-0 select-none", className)}
    />
  );
}
