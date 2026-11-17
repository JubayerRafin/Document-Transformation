import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiClient } from "../src/lib/api";
import { GlossaryPanel } from "../src/components/glossary-panel";
import { glossaryCsv, glossaryProblem, parseGlossaryCsv } from "../src/lib/glossary";

// jsdom's File has no text(); every supported browser does.
if (!Blob.prototype.text)
  Blob.prototype.text = function (this: Blob) {
    return new Promise<string>((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.readAsText(this);
    });
  };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

describe("glossary CSV helpers", () => {
  it("reads comma, semicolon and tab files with quotes and a header", () => {
    expect(
      parseGlossaryCsv('﻿source,target\nredaction,Schwärzung\n"Board, Audit";"Prüfungsausschuss"\nID\tID\n\n'),
    ).toEqual([
      { source: "redaction", target: "Schwärzung" },
      { source: "Board, Audit", target: "Prüfungsausschuss" },
      { source: "ID", target: "ID" },
    ]);
  });
  it("skips rows without a translation", () =>
    expect(parseGlossaryCsv("alpha,\nbeta,Beta")).toEqual([{ source: "beta", target: "Beta" }]));
  it("round-trips through export", () => {
    const entries = [
      { source: 'say "hi"', target: "a,b" },
      { source: "plain", target: "einfach" },
    ];
    expect(parseGlossaryCsv(glossaryCsv(entries))).toEqual(entries);
  });
  it("explains what the server would refuse", () => {
    expect(glossaryProblem([{ source: "", target: "" }])).toBe("");
    expect(glossaryProblem([{ source: "a", target: "" }])).toMatch(/both/);
    expect(
      glossaryProblem([
        { source: "Term", target: "x" },
        { source: "term ", target: "y" },
      ]),
    ).toMatch(/twice/);
    expect(glossaryProblem([{ source: "a".repeat(201), target: "b" }])).toMatch(/200/);
    expect(
      glossaryProblem(Array.from({ length: 101 }, (_, i) => ({ source: `t${i}`, target: "x" }))),
    ).toMatch(/100/);
  });
});

describe("glossary API", () => {
  it("reads one pair and saves with CSRF", async () => {
    const transport = vi
      .fn()
      .mockResolvedValue(json({ source_language: "en", target_language: "de", entries: [], updated: null }));
    const api = new ApiClient(transport);
    api.csrf = "token";
    await api.glossary("en", "de");
    expect(transport.mock.calls[0][0]).toBe("/v1/glossary?source=en&target=de");
    await api.saveGlossary("en", "de", [{ source: "redaction", target: "Schwärzung" }]);
    const [path, init] = transport.mock.calls[1];
    expect(path).toBe("/v1/glossary");
    expect(init.method).toBe("PUT");
    expect(init.headers.get("X-CSRF-Token")).toBe("token");
    expect(JSON.parse(init.body)).toEqual({
      source_language: "en",
      target_language: "de",
      entries: [{ source: "redaction", target: "Schwärzung" }],
    });
  });
});

function server(entries: { source: string; target: string }[] = []) {
  const saved: unknown[] = [];
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    if (path === "/v1/glossaries") return json([]);
    if (init.method === "PUT") {
      const body = JSON.parse(String(init.body));
      saved.push(body);
      return json({ ...body, updated: 1 });
    }
    const q = new URLSearchParams(path.split("?")[1]);
    return json({
      source_language: q.get("source"),
      target_language: q.get("target"),
      entries: q.get("target") === "de" ? entries : [],
      updated: null,
    });
  });
  return { api: new ApiClient(transport), saved, transport };
}

describe("glossary page", () => {
  it("loads the saved terms and saves edits", async () => {
    const u = userEvent.setup(),
      { api, saved } = server([{ source: "redaction", target: "Schwärzung" }]);
    render(<GlossaryPanel api={api} onError={vi.fn()} />);
    expect(await screen.findByDisplayValue("Schwärzung")).toBeTruthy();
    const save = screen.getByRole("button", { name: /Save glossary/ });
    expect(save).toHaveProperty("disabled", true);
    await u.click(screen.getByRole("button", { name: /Add term/ }));
    await u.type(screen.getByLabelText("Term 2 in English"), "Board");
    expect(screen.getByRole("alert").textContent).toMatch(/both/);
    await u.type(screen.getByLabelText("Translation 2 in German"), "Vorstand");
    await u.click(save);
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toEqual({
      source_language: "en",
      target_language: "de",
      entries: [
        { source: "redaction", target: "Schwärzung" },
        { source: "Board", target: "Vorstand" },
      ],
    });
    expect(await screen.findByText(/will use these 2 terms/)).toBeTruthy();
  });
  it("asks before discarding unsaved changes when the pair changes", async () => {
    const u = userEvent.setup(),
      { api, transport } = server();
    render(<GlossaryPanel api={api} onError={vi.fn()} />);
    await u.type(await screen.findByLabelText("Term 1 in English"), "Board");
    await u.selectOptions(screen.getByLabelText("To"), "fr");
    expect(screen.getByText(/unsaved changes for this language pair/)).toBeTruthy();
    expect(transport.mock.calls.some(([p]) => String(p).includes("target=fr"))).toBe(false);
    await u.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() =>
      expect(transport.mock.calls.some(([p]) => String(p).includes("target=fr"))).toBe(true),
    );
  });
  it("imports a CSV file into the table", async () => {
    const u = userEvent.setup(),
      { api } = server();
    render(<GlossaryPanel api={api} onError={vi.fn()} />);
    await screen.findByLabelText("Term 1 in English");
    await u.upload(
      screen.getByLabelText("Import glossary CSV"),
      new File(["source,target\nredaction,Schwärzung\n"], "terms.csv", { type: "text/csv" }),
    );
    expect(await screen.findByDisplayValue("Schwärzung")).toBeTruthy();
    expect(screen.getByText(/1 term imported/)).toBeTruthy();
  });
  it("is read-only for viewers", async () => {
    const { api } = server([{ source: "redaction", target: "Schwärzung" }]);
    render(<GlossaryPanel api={api} onError={vi.fn()} readOnly />);
    expect(await screen.findByDisplayValue("Schwärzung")).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: /Save glossary/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Import CSV/ })).toBeNull();
  });
});
