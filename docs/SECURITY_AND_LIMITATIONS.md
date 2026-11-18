# Security notes and known limitations

## Where documents go

- **Engine.** A job's input, intermediate files and output stay in its job folder on the machine
  that runs the engine. Model requests carry document text to the configured model servers only.
- **Model servers.** By default only servers on the same machine are used. A server elsewhere is
  used only when an operator lists it in `TRANSFORMX_OPENAI_COMPATIBLE`; it must use HTTPS unless
  `TRANSFORMX_ALLOW_HTTP_REMOTE=1` is set for a trusted internal network. Document text then leaves
  the machine for that server, so it must be one the organization trusts with the documents.
- **Site worker.** The hosted website stores uploads and results in the user's private storage; the
  worker downloads the input, processes it on the GPU server and uploads the result. The website
  itself never runs the engine. Local job folders are deleted after 72 hours by default.
- **Engine service.** Listens on `127.0.0.1` unless told otherwise. With an API key it refuses
  unauthenticated requests; without one it has no sign-in and must sit behind a proxy that
  provides it. Finished jobs are deleted after 24 hours by default; `DELETE /v1/jobs/{id}` deletes
  one at once.

## Secrets

- Model API keys, the engine service key and worker credentials are read from the environment or
  from key files (`..._FILE`). They are never written into job options, run folders, logs or the
  settings file the start script writes.
- A model endpoint may not contain a user name, password or query string; keys go in the
  environment instead.
- Worker configs (`*.local.json`), `.env` files and job outputs are ignored by Git.

## PII masking

PII masking finds candidates with rules and a language model, deletes the matched text objects from
the page and re-reads the output (including OCR of images) before a job may finish. It is
**reviewed masking, not certified secure redaction**: detection can miss values, and complete
removal and unrecoverability of every masked value have not been independently verified. Treat
masked output as a draft that a person checks before it is shared.

## Known limitations

- **Review needed.** Every result is a draft with warnings and a review list, not an approved
  translation.
- **Speed.** About 12.6 s per page on an L40S for first-time translations; the 3–5 s target is not
  reached yet.
- **OCR languages.** Scanned pages are read for English, Korean, Chinese and Japanese sources.
  Arabic, Hindi and Bengali scans are not read.
- **Korean sources.** Months and quarters (`11월`, `3분기`, `Q3`) may be written in words by the
  translation; other numbers attached to Korean words stay protected literals, so such a sentence
  can still translate awkwardly. The Korean OCR model sometimes drops spaces.
- **Scanned layouts.** Very small letter-spaced labels on photos stay in the source language;
  headings rebuilt from scans are regular weight unless the scan shows clear bold; a title that
  wraps without punctuation is translated line by line. Text on a picture that overlaps the page's
  own text is kept as it is.
- **Copying text from outputs.** Mixed Arabic and Latin lines can come out of copy and paste in the
  wrong order.
- **Source scripts.** Arabic, Hindi and Bengali PDFs whose text layer is wrong (some generators
  write one) are translated from that wrong text; scans in these scripts are not read.
- **Page limit.** The website accepts PDFs of up to 200 pages and 50 MB; split longer documents.
- **Glossaries.** A forced term can produce awkward compound words (German).
- **Hosting.** The website depends on Cloudflare and Supabase; internal deployments should use the
  engine service instead and provide their own sign-in and storage.
