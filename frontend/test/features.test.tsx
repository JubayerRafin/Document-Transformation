import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, domAnimation } from "motion/react";
import { ApiClient, ApiError } from "../src/lib/api";
import { UploadPanel } from "../src/components/upload-panel";
import { HistoryPanel } from "../src/components/history-panel";
import { ProgressPanel } from "../src/components/progress-panel";
import { PreviewPanel } from "../src/components/preview-panel";
import { SignIn } from "../src/components/sign-in";
import { WorkersPanel } from "../src/components/workers-panel";
import { job, worker } from "./fixtures";
function upload(api = new ApiClient()) {
  const submitted = vi.fn().mockResolvedValue(undefined);
  render(
    <LazyMotion features={domAnimation}>
      <UploadPanel
        api={api}
        workers={[worker]}
        onSubmitted={submitted}
        onServers={vi.fn()}
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
    screen.getByLabelText("Choose a PDF"),
    new File(["%PDF-1.7\n"], "test.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("test.pdf");
  await u.selectOptions(screen.getByLabelText("GPU server"), worker.id);
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
    screen.getByLabelText("Choose a PDF"),
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
    screen.getByLabelText("Choose a PDF"),
    new File(["%PDF-1.7\n"], "retry.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("retry.pdf");
  await u.selectOptions(screen.getByLabelText("GPU server"), worker.id);
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
    screen.getByLabelText("Choose a PDF"),
    new File(["%PDF-1.7\n"], "private.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("private.pdf");
  await u.selectOptions(screen.getByLabelText("GPU server"), worker.id);
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
    screen.getByLabelText("Choose a PDF"),
    new File(["%PDF-1.7\n"], "pii.pdf", { type: "application/pdf" }),
  );
  await screen.findByText("pii.pdf");
  await u.selectOptions(screen.getByLabelText("GPU server"), worker.id);
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
it("sign-in uses the existing API and displays sanitized authentication errors", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "login").mockRejectedValue(new ApiError("INVALID_LOGIN", 401));
  render(<SignIn open onOpenChange={vi.fn()} api={api} onAccount={vi.fn()} />);
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "test@example.com",
  );
  await userEvent.type(screen.getByLabelText("Password"), "bad-password");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("didn’t match");
});
it("registers a private server without displaying the secret token", async () => {
  const api = new ApiClient();
  vi.spyOn(api, "registerWorker").mockResolvedValue({
    id: worker.id,
    token: "never-show-this-secret",
    name: "GPU",
    supabase_url: "https://test.supabase.co",
  });
  render(
    <WorkersPanel
      api={api}
      workers={[]}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onError={vi.fn()}
    />,
  );
  await userEvent.type(screen.getByLabelText("Server name"), "GPU");
  await userEvent.click(
    screen.getByRole("button", { name: "Register server" }),
  );
  expect(
    await screen.findByRole("button", {
      name: "Download private configuration",
    }),
  ).toBeVisible();
  expect(screen.queryByText("never-show-this-secret")).not.toBeInTheDocument();
});
