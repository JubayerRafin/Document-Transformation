import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, domAnimation } from "motion/react";
import { ApiClient, ApiError } from "../src/lib/api";
import { UploadPanel } from "../src/components/upload-panel";
import { HistoryPanel } from "../src/components/history-panel";
import { ProgressPanel } from "../src/components/progress-panel";
import { PreviewPanel } from "../src/components/preview-panel";
import { job, worker } from "./fixtures";
function upload(api = new ApiClient()) {
  const submitted = vi.fn().mockResolvedValue(undefined);
  render(
    <LazyMotion features={domAnimation}>
      <UploadPanel
        api={api}
        workers={[worker]}
        onSubmitted={submitted}
        onError={vi.fn()}
      />
    </LazyMotion>,
  );
  return { api, submitted };
}
it("submits a selected PDF using the exact selected server and mode", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id });
  const { submitted } = upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).toMatchObject({
    worker_id: worker.id,
    operation: "translation",
    output_format: "pdf",
  });
  expect(submitted).toHaveBeenCalledWith(job.id);
});
it("offers the server's translation models and sends the chosen one", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id }),
    withModels = {
      ...worker,
      capabilities: {
        ...worker.capabilities,
        translation_models: [
          { id: "default", label: "Standard (this server)", external: false },
          { id: "openai", label: "OpenAI GPT-5 mini", external: true },
        ],
      },
    };
  render(
    <LazyMotion features={domAnimation}>
      <UploadPanel api={api} workers={[withModels]} onSubmitted={vi.fn()} onError={vi.fn()} />
    </LazyMotion>,
  );
  // This server offers two models, so the choice is shown at once.
  expect(screen.getByLabelText("Translation model")).toBeInTheDocument();
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  const select = screen.getByLabelText("Translation model");
  expect(select).toHaveValue("default");
  await u.selectOptions(select, "openai");
  // The user is told that text leaves the server before starting.
  expect(screen.getByText(/document's text is sent there/i)).toBeVisible();
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).toMatchObject({ translation_model: "openai" });
});
it("sends no model choice for the default model or for PII only", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id });
  upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  expect(screen.queryByLabelText("Translation model")).not.toBeInTheDocument();
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).not.toHaveProperty("translation_model");
});
it("shows category controls for PII and translation controls for combined", async () => {
  const u = userEvent.setup();
  upload();
  await u.click(screen.getByRole("tab", { name: "PII redaction" }));
  expect(
    screen.getByRole("checkbox", { name: "Email addresses" }),
  ).toBeChecked();
  expect(screen.queryByLabelText("Target language")).not.toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "Both" }));
  expect(screen.getByLabelText("Target language")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "Names" })).toBeVisible();
  expect(screen.getByRole("tabpanel")).toHaveTextContent(
    "Translate your document",
  );
});
it("blocks invalid PDF content and never submits", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit");
  upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["invalid"], "fake.pdf", { type: "application/pdf" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("valid PDF");
  expect(submit).not.toHaveBeenCalled();
});
it("reuses the idempotency key after a failed unchanged submission", async () => {
  const u = userEvent.setup(),
    api = new ApiClient();
  const submit = vi
    .spyOn(api, "submit")
    .mockRejectedValueOnce(new ApiError("NETWORK_ERROR", 0))
    .mockResolvedValueOnce({ job_id: job.id });
  upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "retry.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("retry.pdf");
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/ }));
  await screen.findByRole("alert");
  await u.click(screen.getByRole("button", { name: /Start transformation/ }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
  expect(submit.mock.calls[0][2]).toBe(submit.mock.calls[1][2]);
});
it("does not open an uploaded job after its account session changed", async () => {
  const u = userEvent.setup(),
    api = new ApiClient();
  api.csrf = "original-session";
  let resolve!: (v: { job_id: string }) => void;
  vi.spyOn(api, "submit").mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const { submitted } = upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "private.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("private.pdf");
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/ }));
  expect(
    screen.getByRole("button", { name: "Uploading document…" }),
  ).toBeDisabled();
  api.csrf = "";
  resolve({ job_id: job.id });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Start transformation/ }),
    ).toBeEnabled(),
  );
  expect(submitted).not.toHaveBeenCalled();
});
it("requires a selected PII category before sending a redaction job", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit");
  upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "pii.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("pii.pdf");
  await u.click(screen.getByRole("tab", { name: "PII redaction" }));
  for (const name of ["Names", "Email addresses", "Phone numbers"])
    await u.click(screen.getByRole("checkbox", { name }));
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "at least one PII category",
  );
  expect(submit).not.toHaveBeenCalled();
});
it("filters history and opens the matching job without claiming approval", async () => {
  const u = userEvent.setup(),
    select = vi.fn();
  render(
    <HistoryPanel
      jobs={[
        job,
        { ...job, id: "other", filename: "Other.pdf", status: "failed" },
      ]}
      onSelect={select}
      onNew={vi.fn()}
    />,
  );
  await u.type(screen.getByLabelText("Search documents"), "synthetic");
  expect(screen.queryByText("Other.pdf")).not.toBeInTheDocument();
  await u.click(screen.getByRole("button", { name: "Open Synthetic.pdf" }));
  expect(select).toHaveBeenCalledWith(job);
  expect(screen.getByText("Generated", { selector: "span" })).toBeVisible();
});
it("only offers cancellation for queued, non-baseline jobs and shows recorded stages", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "events").mockResolvedValue([
    { id: 1, stage: "extraction", elapsed_seconds: 4, created: job.created },
  ]);
  const cancel = vi.spyOn(api, "cancel").mockResolvedValue({}),
    refresh = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(
    <ProgressPanel
      job={{ ...job, status: "queued" }}
      api={api}
      onRefresh={refresh}
      onResult={vi.fn()}
      onError={vi.fn()}
    />,
  );
  expect(await screen.findByText("Extraction")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Cancel queued job" }),
  );
  expect(cancel).toHaveBeenCalledWith(job.id);
  rerender(
    <ProgressPanel
      job={{ ...job, status: "running" }}
      api={api}
      onRefresh={refresh}
      onResult={vi.fn()}
      onError={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Cancel queued job" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});
it("previews the matched source/output artifacts and keeps authorized download links", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "preview").mockImplementation(async (_, id) => ({
    url: `https://test.supabase.co/${id}`,
  }));
  render(<PreviewPanel job={job} api={api} onError={vi.fn()} />);
  expect(await screen.findByTitle("Original document")).toHaveAttribute(
    "src",
    `https://test.supabase.co/${job.artifacts[0].id}#page=1`,
  );
  expect(screen.getByTitle("Transformed document")).toHaveAttribute(
    "src",
    `https://test.supabase.co/${job.artifacts[1].id}#page=1`,
  );
  expect(screen.getByRole("link", { name: "Download output" })).toHaveAttribute(
    "href",
    `/v1/jobs/${job.id}/artifacts/${job.artifacts[1].id}`,
  );
});
it("sends the chosen masking style and keep-visible values with a PII job", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id });
  upload(api);
  await u.click(screen.getByRole("tab", { name: "PII redaction" }));
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  await u.selectOptions(screen.getByLabelText("How removed text looks"), "pseudonym");
  await u.type(screen.getByLabelText("Keep visible, one per line"), "Northwind Supply{enter}  {enter}Head office");
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).toMatchObject({
    operation: "pii",
    pii_style: "pseudonym",
    pii_keep: ["Northwind Supply", "Head office"],
  });
});
it("translation jobs do not send PII masking options", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id });
  upload(api);
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).not.toHaveProperty("pii_style");
  expect(submit.mock.calls[0][1]).not.toHaveProperty("pii_keep");
});
it("summarizes the review queue and links to the review editor", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "preview").mockResolvedValue({ url: "https://test.supabase.co/x" });
  const box = { page: 1, x: 0.1, y: 0.1, width: 0.2, height: 0.05, category: "Names" };
  const queued = {
    ...job,
    options: { ...job.options, operation: "pii" as const },
    metrics: {
      ...job.metrics,
      checks: { acceptable: true, privacy_verification: "passed" as const, known_sensitive_residuals: 0, geometry_blockers: 0,
        layout_review_candidates: 0, uninspected_content: 0, scan_acceptance_blockers: 0, translation_errors: 0 },
      review_items: [
        { ...box, reason: "rule_overruled_model" as const, state: "suggested" as const },
        { ...box, reason: "unreadable_text" as const, state: "suggested" as const },
        { ...box, reason: "whole_line" as const, state: "masked" as const },
      ],
    },
  };
  render(<PreviewPanel job={queued} api={api} onError={vi.fn()} />);
  const note = await screen.findByTestId("review-queue");
  expect(note).toHaveTextContent("2 possible misses to check · 1 area masked as a precaution");
  expect(screen.getByRole("link", { name: "Open the review" })).toHaveAttribute(
    "href",
    `/redaction-review.html?job=${queued.id}&new=1`,
  );
});
it("summarizes what was masked and how it was found", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "preview").mockResolvedValue({ url: "https://test.supabase.co/x" });
  const box = { page: 1, x: 0.1, y: 0.1, width: 0.2, height: 0.05 };
  const masked = {
    ...job,
    options: { ...job.options, operation: "pii" as const },
    metrics: {
      ...job.metrics,
      checks: { acceptable: true, privacy_verification: "passed" as const, known_sensitive_residuals: 0, geometry_blockers: 0,
        layout_review_candidates: 0, uninspected_content: 0, scan_acceptance_blockers: 0, translation_errors: 0 },
      redaction_map: [
        { ...box, category: "Names", method: "field_label" },
        { ...box, category: "Names", method: "name_variant" },
        { ...box, category: "Email addresses", method: "pattern" },
        { ...box, category: "Identifiers", method: "model_proposal" },
      ],
    },
  };
  render(<PreviewPanel job={masked} api={api} onError={vi.fn()} />);
  expect(await screen.findByTestId("redaction-summary")).toHaveTextContent(
    "4 areas masked: 2 Names, 1 Email addresses, 1 Identifiers · found by: field label or format 2, language model 1, name list or consistency 1",
  );
});
it("offers the server's PII models for redaction and warns before an outside service", async () => {
  const u = userEvent.setup(),
    api = new ApiClient(),
    submit = vi.spyOn(api, "submit").mockResolvedValue({ job_id: job.id }),
    withPiiModels = {
      ...worker,
      capabilities: {
        ...worker.capabilities,
        pii_models: [
          { id: "default", label: "Qwen 3.5 9B", external: false },
          { id: "openai", label: "OpenAI GPT-5 mini", external: true },
        ],
      },
    };
  render(
    <LazyMotion features={domAnimation}>
      <UploadPanel api={api} workers={[withPiiModels]} onSubmitted={vi.fn()} onError={vi.fn()} />
    </LazyMotion>,
  );
  // Translation only: no PII model choice.
  expect(screen.queryByLabelText("PII model")).not.toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "PII redaction" }));
  await u.upload(
    screen.getByLabelText("Choose a PDF, Word or PowerPoint file"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  const select = screen.getByLabelText("PII model");
  expect(select).toHaveValue("default");
  await u.selectOptions(select, "openai");
  expect(screen.getByText(/including the personal data to be removed/i)).toBeVisible();
  await u.click(screen.getByRole("checkbox", { name: /authorized/i }));
  await u.click(screen.getByRole("button", { name: /Start transformation/i }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][1]).toMatchObject({ operation: "pii", pii_model: "openai" });
  expect(submit.mock.calls[0][1]).not.toHaveProperty("translation_model");
});
