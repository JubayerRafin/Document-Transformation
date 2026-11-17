// Customer glossary: the account's required translations for its own terms, per language pair.
export type GlossaryEntry = { source: string; target: string };
export type Glossary = {
  source_language: string;
  target_language: string;
  entries: GlossaryEntry[];
  updated: number | null;
};
export type GlossarySummary = {
  source_language: string;
  target_language: string;
  terms: number;
  updated: number;
};
// Matches the server and engine limits (cloud/src/index.mjs glossaryEntries).
export const GLOSSARY_LIMIT = 100;
export const TERM_LIMIT = 200;

function cells(line: string) {
  const out: string[] = [];
  let value = "",
    quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        value += '"';
        i++;
      } else if (c === '"') quoted = false;
      else value += c;
    } else if (c === '"') quoted = true;
    else if (c === "," || c === ";" || c === "\t") {
      out.push(value);
      value = "";
    } else value += c;
  }
  out.push(value);
  return out.map((v) => v.trim());
}

/** Two columns (term, translation), comma/semicolon/tab separated, optional header row. */
export function parseGlossaryCsv(text: string): GlossaryEntry[] {
  const rows = text
    .replace(/^﻿/, "")
    .split(/\r?\n/)
    .filter((l) => l.trim())
    .map(cells);
  if (rows.length && /^(source|term|english)$/i.test(rows[0][0] || "")) rows.shift();
  return rows
    .filter((r) => r[0] && r[1])
    .map(([source, target]) => ({ source, target }));
}

export function glossaryCsv(entries: GlossaryEntry[]) {
  const quote = (v: string) => (/[",;\t]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return ["source,target", ...entries.map((e) => `${quote(e.source)},${quote(e.target)}`)].join("\n") + "\n";
}

/** Problems that would make the server refuse the list, in plain words, or "" when it is valid. */
export function glossaryProblem(entries: GlossaryEntry[]) {
  const filled = entries.filter((e) => e.source.trim() || e.target.trim());
  if (filled.length > GLOSSARY_LIMIT) return `A glossary can hold at most ${GLOSSARY_LIMIT} terms.`;
  const seen = new Set<string>();
  for (const e of filled) {
    const source = e.source.trim(),
      target = e.target.trim();
    if (!source || !target) return "Every term needs both the original wording and its translation.";
    if (source.length > TERM_LIMIT || target.length > TERM_LIMIT)
      return `Terms can be at most ${TERM_LIMIT} characters.`;
    if (seen.has(source.toLowerCase())) return `“${source}” appears twice.`;
    seen.add(source.toLowerCase());
  }
  return "";
}
