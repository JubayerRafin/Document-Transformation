import { it, expect, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useWorkspace } from "../src/hooks/use-workspace";
import { ApiClient, ApiError } from "../src/lib/api";
import { job, worker } from "./fixtures";
it("clears document records when the session expires", async () => {
  vi.spyOn(ApiClient.prototype, "me").mockImplementation(async function (
    this: ApiClient,
  ) {
    this.csrf = "csrf-a";
    return { name: "test@example.com", role: "customer", csrf: "csrf-a" };
  });
  vi.spyOn(ApiClient.prototype, "jobs").mockResolvedValue([job]);
  vi.spyOn(ApiClient.prototype, "workers").mockResolvedValue([worker]);
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.jobs).toHaveLength(1));
  act(() => result.current.handleError(new ApiError("SIGN_IN_REQUIRED", 401)));
  expect(result.current.jobs).toHaveLength(0);
  expect(result.current.workers).toHaveLength(0);
  expect(result.current.account).toBeNull();
  expect(result.current.signIn).toBe(true);
});
it("ignores an in-flight history response after logout", async () => {
  vi.spyOn(ApiClient.prototype, "me").mockImplementation(async function (
    this: ApiClient,
  ) {
    this.csrf = "csrf-b";
    return { name: "test@example.com", role: "customer", csrf: "csrf-b" };
  });
  let resolve!: (v: (typeof job)[]) => void;
  vi.spyOn(ApiClient.prototype, "jobs").mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  vi.spyOn(ApiClient.prototype, "workers").mockResolvedValue([worker]);
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.account).not.toBeNull());
  await waitFor(() => expect(resolve).toBeDefined());
  act(() => result.current.handleError(new ApiError("SIGN_IN_REQUIRED", 401)));
  await act(async () => {
    resolve([job]);
  });
  expect(result.current.jobs).toHaveLength(0);
});
