import { it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useWorkspace } from "../src/hooks/use-workspace";
import { ApiClient, ApiError } from "../src/lib/api";
import { job, worker } from "./fixtures";
function server(csrf: string) {
  vi.spyOn(ApiClient.prototype, "me").mockImplementation(async function (this: ApiClient) {
    this.csrf = csrf;
    return { name: "This server", role: "admin", csrf };
  });
}
it("loads the server's jobs and status without signing in", async () => {
  server("local");
  vi.spyOn(ApiClient.prototype, "jobs").mockResolvedValue([job]);
  vi.spyOn(ApiClient.prototype, "workers").mockResolvedValue([worker]);
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.jobs).toHaveLength(1));
  expect(result.current.workers).toHaveLength(1);
  expect(result.current.error).toBe("");
});
it("keeps the server status available when document history fails", async () => {
  server("local-c");
  vi.spyOn(ApiClient.prototype, "jobs").mockRejectedValue(new ApiError("INTERNAL_ERROR", 500));
  vi.spyOn(ApiClient.prototype, "workers").mockResolvedValue([worker]);
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.workers).toHaveLength(1));
  expect(result.current.workers[0].id).toBe(worker.id);
  expect(result.current.error).not.toBe("");
  expect(result.current.account).not.toBeNull();
});
it("shows an error instead of a sign-in prompt when the server does not answer", async () => {
  vi.spyOn(ApiClient.prototype, "me").mockRejectedValue(new ApiError("NETWORK_ERROR", 0));
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.account).toBeNull();
  expect(result.current.error).not.toBe("");
});
