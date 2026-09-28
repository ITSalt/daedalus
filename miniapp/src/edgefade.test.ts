import { describe, expect, it } from "vitest";
import { fadeFor, moreBelow } from "./edgefade";

describe("a sideways row's fade", () => {
  it("is none for a row that fits, allowing a pixel of rounding", () => {
    expect(fadeFor(0, 390, 390)).toBe("");
    expect(fadeFor(0, 390, 390.6)).toBe("");
  });

  it("marks the edge with more behind it", () => {
    expect(fadeFor(0, 390, 700)).toBe("end");
    expect(fadeFor(310, 390, 700)).toBe("start");
    expect(fadeFor(120, 390, 700)).toBe("both");
  });
});

describe("a column's fold", () => {
  it("has more below until it is scrolled to the end", () => {
    expect(moreBelow(0, 618, 802)).toBe(true);
    expect(moreBelow(184, 618, 802)).toBe(false);
    expect(moreBelow(0, 618, 618)).toBe(false);
  });
});
