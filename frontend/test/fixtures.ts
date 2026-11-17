import type { Job, Worker } from "../src/lib/api";
export const worker: Worker = {
  id: "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
  name: "Test GPU",
  enabled: true,
  ready: true,
  online: true,
  capabilities: {
    gpu: "Synthetic GPU",
    operations: ["translation", "pii", "combined"],
  },
};
export const job: Job = {
  id: "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
  filename: "Synthetic.pdf",
  status: "succeeded",
  stage: "complete",
  quality: "requires_review",
  pages: 2,
  created: 1792600000,
  updated: 1792600020,
  baseline: false,
  source_sha256: "source-hash",
  options: {
    operation: "translation",
    source_language: "en",
    target_language: "ko",
    output_format: "pdf",
    protected_values: [],
    document_context: "",
    pii_categories: [],
    worker_id: worker.id,
  },
  metrics: { server_seconds: 20, evaluation_status: "pending" },
  artifacts: [
    {
      id: "cccccccc-cccc-4ccc-cccc-cccccccccccc",
      kind: "source",
      filename: "Synthetic.pdf",
      sha256: "source-hash",
      size: 100,
    },
    {
      id: "dddddddd-dddd-4ddd-dddd-dddddddddddd",
      kind: "result",
      filename: "Synthetic-ko.pdf",
      sha256: "result-hash",
      size: 100,
    },
  ],
};
