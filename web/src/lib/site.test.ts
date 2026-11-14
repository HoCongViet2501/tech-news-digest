import { describe, expect, it } from "vitest";

import { site } from "./site";

describe("site", () => {
  it("has a name and a tagline", () => {
    expect(site.name).toBe("Tech News");
    expect(site.tagline.length).toBeGreaterThan(0);
  });
});
