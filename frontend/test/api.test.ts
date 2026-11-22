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
      { ...job.options, operation: "translation" },
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
    const large = { name: "large.pdf", size: 100_000_001 } as File;
    expect(await validatePdf(large)).toContain("100 MB");
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
import { pdfFacts, MAX_PAGES } from "../src/lib/api";
it("refuses PDFs over the page limit or with a password before upload", async () => {
  const pdf = new File(["%PDF-1.7\n"], "manual.pdf");
  expect(await validatePdf(pdf, async () => ({ pages: 312 }))).toBe(
    `This PDF has 312 pages; the limit is ${MAX_PAGES}. Split it into parts of up to ${MAX_PAGES} pages.`,
  );
  expect(await validatePdf(pdf, async () => ({ pages: MAX_PAGES }))).toBeNull();
  expect(await validatePdf(pdf, async () => ({ encrypted: true }))).toContain("password-protected");
  // When pdf.js cannot read the file here, the server's own check decides.
  expect(await validatePdf(pdf, async () => ({}))).toBeNull();
});
it("reads the page count with pdf.js and recognises a password", async () => {
  const pdf = new File(["%PDF-1.7\n"], "manual.pdf");
  const fake = (result: () => Promise<{ numPages: number; destroy: () => Promise<void> }>) => async () => ({
    GlobalWorkerOptions: { workerSrc: "" },
    getDocument: () => ({ promise: result() }),
  });
  expect(await pdfFacts(pdf, fake(async () => ({ numPages: 250, destroy: async () => {} })))).toEqual({ pages: 250 });
  const locked = Object.assign(new Error("No password given"), { name: "PasswordException" });
  expect(await pdfFacts(pdf, fake(async () => Promise.reject(locked)))).toEqual({ encrypted: true });
  expect(await pdfFacts(pdf, async () => Promise.reject(new Error("no pdf.js")))).toEqual({});
});

import { passedChecks, checkFindings, type JobChecks } from "../src/lib/api";
describe("result badge", () => {
  const clean: JobChecks = {
    acceptable: true, privacy_verification: "not_needed", known_sensitive_residuals: 0, geometry_blockers: 0,
    layout_review_candidates: 0, uninspected_content: 0, scan_acceptance_blockers: 0, translation_errors: 0,
  };
  it("says the checks passed only when nothing was found", () => {
    expect(passedChecks(clean)).toBe(true);
    expect(checkFindings(clean)).toEqual([]);
    expect(passedChecks({ ...clean, layout_review_candidates: 2 })).toBe(false);
    expect(passedChecks({ ...clean, acceptable: false })).toBe(false);
    expect(passedChecks({ ...clean, privacy_verification: "failed" })).toBe(false);
    expect(passedChecks(undefined)).toBe(false);
  });
});
