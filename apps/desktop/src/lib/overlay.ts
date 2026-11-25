import { invoke } from "@tauri-apps/api/core";
import { useEffect, type RefObject } from "react";
import type { Lesson } from "@/lib/corrections";

export type OverlayWindow = "live" | "recents";

export type LiveEdit = {
  session: number;
  base: string;
  text: string;
  steps: { segment: number; text: string; category: Lesson | null }[];
};

export const overlayApi = {
  /** Size the panel to its content and show it; the bottom edge stays put. */
  fit: (window: OverlayWindow, height: number, animate = true) =>
    invoke<void>("overlay_fit", { request: { window, height, animate } }),
  hide: (window: OverlayWindow) => invoke<void>("overlay_hide", { window }),
  /** Give the keyboard back to the application the user was typing in. */
  release: () => invoke<void>("overlay_release"),
  insert: (text: string) => invoke<string>("recents_insert", { text }),
  liveEdit: (edit: LiveEdit) => invoke<void>("live_edit", { edit }),
};

/**
 * Keep the panel as tall as `ref`'s content while `active`. The panel starts hidden,
 * and a hidden window draws no frames: no animation frame and no resize callback
 * would ever arrive. So the first measure is synchronous, and a light poll covers
 * changes made before the panel is on screen.
 */
export function useFitToContent(
  window: OverlayWindow,
  ref: RefObject<HTMLElement | null>,
  active: boolean,
) {
  useEffect(() => {
    const element = ref.current;
    if (!active || !element) return;
    let last = -1;
    const measure = () => {
      const height = Math.ceil(element.getBoundingClientRect().height);
      if (height === last || height === 0) return;
      last = height;
      void overlayApi.fit(window, height).catch(() => {
        last = -1;
      });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    const poll = setInterval(measure, 250);
    measure();
    return () => {
      observer.disconnect();
      clearInterval(poll);
    };
  }, [window, ref, active]);
}
