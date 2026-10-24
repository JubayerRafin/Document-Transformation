// LOCAL UI FIXTURE ONLY. Never deploy this server. No Supabase or GPU requests.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import worker from "../../cloud/src/index.mjs";
const root = path.resolve(fileURLToPath(new URL("../../cloud/public/", import.meta.url)));
const origin = "http://127.0.0.1:8787";
const id = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
  wid = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
const gpu = {
  id: wid,
  name: "Synthetic test server",
  enabled: true,
  online: true,
  ready: true,
  capabilities: {
    gpu: "Test fixture — no inference",
    operations: ["translation", "pii", "combined"],
  },
};
const options = {
  operation: "translation",
  source_language: "en",
  target_language: "ko",
  output_format: "pdf",
  protected_values: [],
  document_context: "",
  pii_categories: [],
  worker_id: wid,
};
const artifacts = ["source", "result"].map((kind, i) => ({
  id: `${i ? "dddddddd-dddd-4ddd-dddd-dddddddddddd" : "cccccccc-cccc-4ccc-cccc-cccccccccccc"}`,
  kind,
  filename: kind === "source" ? "Synthetic.pdf" : "Synthetic-output.pdf",
  sha256: "test-fixture",
  size: 600,
}));
const job = {
  id,
  filename: "Synthetic.pdf",
  status: "succeeded",
  stage: "complete",
  quality: "requires_review",
  pages: 1,
  created: 1792600000,
  updated: 1792600020,
  baseline: false,
  options,
  metrics: { server_seconds: 20, evaluation_status: "pending" },
  artifacts,
  source_sha256: "test-fixture",
};
let jobs = [job],
  signedIn = true;
const json = (value, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
function pdf() {
  const stream = "BT /F1 18 Tf 50 740 Td (Synthetic UI test document) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let body = "%PDF-1.4\n",
    offsets = [0];
  for (let i = 0; i < objects.length; i++) {
    offsets.push(body.length);
    body += `${i + 1} 0 obj\n${objects[i]}\nendobj\n`;
  }
  const start = body.length;
  body +=
    `xref\n0 6\n0000000000 65535 f \n` +
    offsets
      .slice(1)
      .map((n) => `${String(n).padStart(10, "0")} 00000 n \n`)
      .join("") +
    `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;
  return body;
}
async function fixture(req) {
  const p = new URL(req.url).pathname;
  if (p === "/v1/session" && req.method === "POST") {
    signedIn = true;
    return json({
      name: "demo@example.test",
      role: "mentor",
      csrf: "fixture-csrf",
    });
  }
  if (p === "/v1/session" && req.method === "DELETE") {
    signedIn = false;
    return json({ status: "signed_out" });
  }
  if (!signedIn) return json({ error: "SIGN_IN_REQUIRED" }, 401);
  if (p === "/v1/me")
    return json({
      name: "demo@example.test",
      role: "mentor",
      csrf: "fixture-csrf",
    });
  if(p === "/v1/system") return json({database:"synthetic-fixture",worker:"available",gpu_verified:false,software_version:"UI fixture only"}); if(p === "/v1/baselines") return json([]); if (p === "/v1/workers") return json([gpu]);
  if (p === "/v1/jobs" && req.method === "POST") {
    const form = await req.formData();
    const opts = JSON.parse(String(form.get("options")));
    const file = form.get("file");
    const added = {
      ...job,
      id: crypto.randomUUID(),
      filename: file.name,
      options: opts,
      status: "queued",
      stage: "queued",
      metrics: {},
      artifacts: [artifacts[0]],
    };
    jobs = [added, ...jobs];
    return json({ job_id: added.id, status_url: "/v1/jobs/" + added.id }, 202);
  }
  if (p === "/v1/jobs") return json(jobs);
  const match = p.match(/^\/v1\/jobs\/([^/]+)(?:\/(.*))?$/);
  if (match) {
    const j = jobs.find((j) => j.id === match[1]);
    if (!j) return json({ error: "NOT_FOUND" }, 404);
    const sub = match[2];
    if (!sub) return json(j);
    if (sub === "events")
      return json(
        j.status === "queued"
          ? []
          : [
              {
                id: 1,
                stage: "extraction",
                elapsed_seconds: 2,
                created: job.created,
              },
              {
                id: 2,
                stage: "translation_and_reconstruction",
                elapsed_seconds: 18,
                created: job.created,
              },
              {
                id: 3,
                stage: "complete",
                elapsed_seconds: 20,
                created: job.updated,
              },
            ],
      );
    if (sub === "cancel" && req.method === "POST") {
      j.status = "cancelled";
      j.stage = "cancelled";
      return json({ status: "cancelled" });
    }
    if (sub === "reviews") return json([]); if (sub === "report") return json(j);
    if (sub?.endsWith("/url")) return json({ url: origin + "/fixture.pdf" });
    if (sub?.startsWith("artifacts/"))
      return new Response(pdf(), {
        headers: { "content-type": "application/pdf" },
      });
  }
  return json({ error: "NOT_FOUND" }, 404);
}
createServer(async (req, res) => {
  try {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const request = new Request(origin + req.url, {
      method: req.method,
      headers: req.headers,
      ...(!["GET", "HEAD"].includes(req.method)
        ? { body: Buffer.concat(chunks) }
        : {}),
    });
    const p = new URL(request.url).pathname;
    let response;
    if (p.startsWith("/v1/")) response = await fixture(request);
    else if (p === "/fixture.pdf")
      response = new Response(pdf(), {
        headers: { "content-type": "application/pdf" },
      });
    else
      response = await worker.fetch(request, {
        SUPABASE_URL: origin,
        SUPABASE_PUBLISHABLE_KEY: "fixture-only",
        ASSETS: {
          fetch: async (r) => {
            let pathname = decodeURIComponent(new URL(r.url).pathname);
            if (pathname === "/") pathname = "/index.html";
            if (!path.extname(pathname)) pathname += ".html";
            const file = path.resolve(root, "." + pathname);
            if (!file.startsWith(root + path.sep))
              return new Response("Not found", { status: 404 });
            try {
              const body = await readFile(file);
              const ext = path.extname(file);
              return new Response(body, {
                headers: {
                  "content-type":
                    {
                      ".html": "text/html",
                      ".css": "text/css",
                      ".js": "text/javascript",
                      ".jpg": "image/jpeg",
                      ".txt": "text/plain",
                    }[ext] || "application/octet-stream",
                },
              });
            } catch {
              return new Response("Not found", { status: 404 });
            }
          },
        },
      });
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(500);
    res.end("Preview error");
  }
}).listen(8787, "127.0.0.1", () =>
  console.log("Synthetic UI fixture only: " + origin),
);
