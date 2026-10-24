import { describe, it, expect, vi } from "vitest";
import { ApiClient, ApiError, validatePdf, downloadUrl } from "../src/lib/api";
import { job } from "./fixtures";
describe("existing API contract", () => {
  it("uses session cookies, safe errors and exact sign-in fields", async () => {
    const transport = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          name: "test@example.com",
          role: "customer",
          csrf: "csrf",
        }),
      ),
    );
    const api = new ApiClient(transport);
    await api.login("test@example.com", "secret");
    expect(transport).toHaveBeenCalledWith(
      "/v1/session",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
        method: "POST",
        body: JSON.stringify({ name: "test@example.com", password: "secret" }),
      }),
    );
    expect(api.csrf).toBe("csrf");
  });
  it("submits multipart data with CSRF and caller idempotency key", async () => {
    const transport = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ job_id: job.id }), { status: 202 }),
      );
    const api = new ApiClient(transport);
    api.csrf = "session-csrf";
    await api.submit(
      new File(["%PDF-test"], "sample.pdf"),
      job.options,
      "stable-key",
    );
    const [path, init] = transport.mock.calls[0];
    expect(path).toBe("/v1/jobs");
    expect(init.headers.get("X-CSRF-Token")).toBe("session-csrf");
    expect(init.headers.get("Idempotency-Key")).toBe("stable-key");
    expect(JSON.parse(init.body.get("options"))).toEqual(job.options);
    expect(init.headers.has("Content-Type")).toBe(false);
  });
  it("does not echo document contents from backend errors", async () => {
    const api = new ApiClient(
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "private document text" }), {
          status: 500,
        }),
      ),
    );
    await expect(api.jobs()).rejects.toThrow("We couldn’t complete");
    await expect(api.jobs()).rejects.not.toThrow("private document text");
  });
  it("retains existing artifact download paths", () =>
    expect(downloadUrl(job, job.artifacts[1])).toBe(
      `/v1/jobs/${job.id}/artifacts/${job.artifacts[1].id}`,
    ));
  it("checks PDF signature and size before submission", async () => {
    expect(
      await validatePdf(new File(["%PDF-1.7\n"], "sample.pdf")),
    ).toBeNull();
    expect(await validatePdf(new File(["not pdf"], "sample.pdf"))).toContain(
      "valid PDF",
    );
    expect(await validatePdf(new File(["%PDF-"], "sample.txt"))).toContain(
      "PDF",
    );
    const large = { name: "large.pdf", size: 50_000_001 } as File;
    expect(await validatePdf(large)).toContain("50 MB");
  });
  it("retains cancellation authorization errors", async () => {
    const api = new ApiClient(
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: "ONLY_QUEUED_CANCELLATION_SUPPORTED" }),
            { status: 409 },
          ),
        ),
    );
    await expect(api.cancel(job.id)).rejects.toBeInstanceOf(ApiError);
  });
});
it('signs out with session cookies and CSRF, retaining retry state on failure',async()=>{
 const transport=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({error:'DATABASE_OR_STORAGE_REQUEST_FAILED'}),{status:503})).mockResolvedValueOnce(new Response(null,{status:204}));
 const api=new ApiClient(transport);api.csrf='session-csrf';
 await expect(api.logout()).rejects.toBeInstanceOf(ApiError);expect(api.csrf).toBe('session-csrf');
 await api.logout();const [path,init]=transport.mock.calls[1];
 expect(path).toBe('/v1/session');expect(init.method).toBe('DELETE');expect(init.credentials).toBe('same-origin');expect(init.headers.get('X-CSRF-Token')).toBe('session-csrf');expect(api.csrf).toBe('');
});
