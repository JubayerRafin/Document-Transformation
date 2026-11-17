import { describe, it, expect } from "vitest";
import { checkFindings, failureMessage } from "../src/lib/api";

describe("job failure reasons and automated checks", () => {
  it("explains known failure codes and falls back for unknown ones", () => {
    expect(failureMessage("WORKER_HEARTBEAT_LOST")).toMatch(/stopped responding/);
    expect(failureMessage("REDACTION_REVIEW_REQUIRED")).toMatch(/no output was released/);
    expect(failureMessage("SOMETHING_NEW")).toMatch(/did not complete successfully/);
    expect(failureMessage(undefined)).toMatch(/did not complete successfully/);
  });
  it("lists only non-zero check counts", () => {
    expect(checkFindings(undefined)).toEqual([]);
    expect(
      checkFindings({
        acceptable: false,
        privacy_verification: "failed",
        known_sensitive_residuals: 0,
        geometry_blockers: 2,
        layout_review_candidates: 0,
        uninspected_content: 1,
        scan_acceptance_blockers: 0,
        translation_errors: 0,
      }),
    ).toEqual(["2 layout problems", "1 images or attachments not checked automatically"]);
  });
});
