// When a conversation lets go of its end. The orchestrator's chat once opened in the middle every
// time: the pin's own scroll event arrived after the windowed history had grown under it, read as the
// reader leaving, and nothing pinned it again.

import { describe, expect, it } from "vitest";
import { stillAtEnd } from "./virtual";

describe("the pin to the end", () => {
  it("holds through the pin's own scroll, even when the history grew before the event came", () => {
    expect(stillAtEnd({ pinned: true, gap: 26_899, top: 16_807, lastTop: 0, hand: false })).toBe(true);
    expect(stillAtEnd({ pinned: true, gap: 900, top: 4_000, lastTop: 4_000, hand: false })).toBe(true);
  });

  it("lets go when the reader moves up, or has a hand on the list", () => {
    expect(stillAtEnd({ pinned: true, gap: 600, top: 3_400, lastTop: 4_000, hand: false })).toBe(false);
    expect(stillAtEnd({ pinned: true, gap: 600, top: 4_200, lastTop: 4_000, hand: true })).toBe(false);
  });

  it("takes the end again whenever the reader is near it, and stays let go on the way down until then", () => {
    expect(stillAtEnd({ pinned: false, gap: 20, top: 9_000, lastTop: 8_000, hand: true })).toBe(true);
    expect(stillAtEnd({ pinned: false, gap: 800, top: 8_000, lastTop: 7_000, hand: false })).toBe(false);
  });
});
