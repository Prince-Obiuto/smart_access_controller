import { expect, test } from "vitest";
import { lagosDate, lagosDateTime, administrativeDate } from "../src/dates";

test("Lagos date fields show midnight on its local calendar date", () => {
  expect(lagosDate("2026-10-12T23:00:00Z")).toBe("2026-10-13");
  expect(lagosDate("2026-12-31T22:59:59Z")).toBe("2026-12-31");
  expect(lagosDate()).toBe("");
  expect(lagosDateTime("2026-12-31T22:59:59Z")).toBe("2026-12-31 23:59:59 Lagos");
});

test("unrelated person edits retain the exact validity bounds", () => {
  const end = "2026-12-31T22:59:59Z";
  expect(administrativeDate("2026-12-31", end)).toBe(end);
  expect(administrativeDate("2026-10-13", "2026-10-12T23:00:00Z")).toBe("2026-10-12T23:00:00Z");
  expect(administrativeDate("2027-01-02", end)).toBe("2027-01-02T00:00:00+01:00");
  expect(administrativeDate("")).toBeUndefined();
});
