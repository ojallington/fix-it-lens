/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * This interface was referenced by `Judge`'s JSON-Schema
 * via the `definition` "Result".
 */
export type Result = "done" | "not_done" | "cant_see";

/**
 * JudgeOutput is the judge's structured output (text.format, strict). Verdict is derived from it by code, with the SAM witness; the model never writes a Verdict.
 */
export interface Judge {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `Judge`'s JSON-Schema
 * via the `definition` "Clause".
 */
export interface Clause {
  clause_id: string;
  visible: boolean;
  state: "satisfied" | "not_satisfied" | "unclear";
  evidence: string;
  /**
   * [x, y] on the 0-1000 grid of NOW_1
   */
  point: null | [number, number];
}
/**
 * This interface was referenced by `Judge`'s JSON-Schema
 * via the `definition` "JudgeOutput".
 */
export interface JudgeOutput {
  clauses: Clause[];
}
/**
 * This interface was referenced by `Judge`'s JSON-Schema
 * via the `definition` "Witnesses".
 */
export interface Witnesses {
  /**
   * The judge's finding; absent only when by_person is true
   */
  judge?: "done" | "not_done" | "cant_see";
  /**
   * Which second witness ran: SAM on video or a frame, none, or (local mode beside Glimmer) track = a separate Glimmer locate on NOW_2 plus the phone's motion track, no SAM
   */
  sam: "video" | "frame" | "none" | "track";
  sam_result?: null | Result;
  /**
   * Absent means false. True only on a fixit step (ours, with nothing for a camera to check): the person ended it with a tap or by saying done, and no judge or witness ran. Never on a manual step
   */
  by_person?: true;
}
/**
 * This interface was referenced by `Judge`'s JSON-Schema
 * via the `definition` "Verdict".
 */
export interface Verdict {
  step_id?: string;
  check_id?: string;
  attempt?: number;
  result: Result;
  /**
   * The clauses that decided the result (missing or unclear ones for not_done / cant_see)
   */
  clause_ids: string[];
  witnesses: Witnesses;
  latency_ms: number;
  ask_for?: "closer" | "side" | "light" | null;
}
