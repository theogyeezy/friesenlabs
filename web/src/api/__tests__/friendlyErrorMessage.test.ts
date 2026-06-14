// Unit test (the trophy's base) for the error-copy contract: users see a friendly sentence, and the
// raw "API <code>" string / bearer token never leak into UI copy.
import { describe, it, expect } from "vitest";
import { ApiError, friendlyErrorMessage } from "../client";

describe("friendlyErrorMessage", () => {
  it("maps well-known statuses to friendly copy", () => {
    expect(friendlyErrorMessage(new ApiError(401, "unauthorized"))).toMatch(/sign in again/i);
    expect(friendlyErrorMessage(new ApiError(403, "forbidden"))).toMatch(/permission/i);
    expect(friendlyErrorMessage(new ApiError(429, "rate"))).toMatch(/too many requests/i);
    expect(friendlyErrorMessage(new ApiError(503, "down"))).toMatch(/isn't available|try again/i);
    expect(friendlyErrorMessage(new ApiError(500, "boom"))).toMatch(/our side|try again/i);
  });

  it("surfaces an author-written 4xx detail but never a raw 'API <code>'", () => {
    const msg = friendlyErrorMessage(new ApiError(422, "Email is required"));
    expect(msg).toBe("Email is required");
    expect(msg).not.toMatch(/API \d+/);
  });

  it("falls back to the provided fallback for unknown errors", () => {
    expect(friendlyErrorMessage(new Error("kaboom"), "Custom fallback")).toBe("Custom fallback");
    expect(friendlyErrorMessage(null)).toMatch(/went wrong/i);
  });
});
