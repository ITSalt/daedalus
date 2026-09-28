// A row that scrolls sideways — a sheet's tabs, a filter strip, a phone's special keys — says so by
// fading the edge that has more behind it. Without it the last item was simply cut by the screen's
// edge ("Wake" for Wake-ups, "Finishe", a key reduced to a sliver) and read as a rendering fault
// rather than as more to swipe to. A sheet's body does the same downwards: a touch screen draws no
// scrollbar, and a hire sheet whose last fields sat below the fold looked complete without them.
// The styles live in styles.css under [data-fade] and [data-more].

import { useEffect, type RefObject } from "react";

/** Which edges of `el` have content scrolled past them: "", "start", "end" or "both". */
export function fadeFor(scrollLeft: number, clientWidth: number, scrollWidth: number): string {
  // A pixel of slack: fractional widths leave scrollWidth a hair above clientWidth on a row that fits.
  const start = scrollLeft > 1;
  const end = scrollLeft + clientWidth < scrollWidth - 1;
  return start && end ? "both" : start ? "start" : end ? "end" : "";
}

/** Whether a column scrolled to `scrollTop` has more below what it shows. */
export function moreBelow(scrollTop: number, clientHeight: number, scrollHeight: number): boolean {
  return scrollTop + clientHeight < scrollHeight - 1;
}

/** Keep `data-fade` on the element current as it scrolls, resizes or gains items. `watch` is
 *  anything that changes what the row holds (a selected tab to bring into view, a count). */
export function useEdgeFade(ref: RefObject<HTMLElement | null>, watch?: unknown): void {
  useWatchScroll(ref, watch, (el) => {
    const fade = fadeFor(el.scrollLeft, el.clientWidth, el.scrollWidth);
    if ((el.dataset.fade ?? "") !== fade) el.dataset.fade = fade;
  });
}

/** Keep `data-more="below"` on a vertically scrolling element while content waits under its fold. */
export function useMoreBelow(ref: RefObject<HTMLElement | null>, watch?: unknown): void {
  useWatchScroll(ref, watch, (el) => {
    const more = moreBelow(el.scrollTop, el.clientHeight, el.scrollHeight) ? "below" : "";
    if ((el.dataset.more ?? "") !== more) el.dataset.more = more;
  });
}

function useWatchScroll(ref: RefObject<HTMLElement | null>, watch: unknown, measure: (el: HTMLElement) => void): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => measure(el);
    update();
    el.addEventListener("scroll", update, { passive: true });
    const resized = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    resized?.observe(el);
    const changed = typeof MutationObserver === "undefined" ? null : new MutationObserver(update);
    changed?.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      el.removeEventListener("scroll", update);
      resized?.disconnect();
      changed?.disconnect();
    };
  }, [ref, watch]);
}
