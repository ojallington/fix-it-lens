/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * [x_min, y_min, x_max, y_max] on the 0-1000 grid of the named frame
 *
 * @minItems 4
 * @maxItems 4
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "Box".
 */
export type Box = [number, number, number, number];
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "RuleId".
 */
export type RuleId = string;
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "ToolName".
 */
export type ToolName =
  "lookup_manual" | "locate" | "segment" | "cite" | "check_step" | "report_hazard" | "ask_for_view" | "ha_state";

/**
 * The guide's eight tools, with the design's names: arguments (strict subset: every key required, optional values nullable) and results. Code validates every argument set anyway: tool arguments are not schema-guaranteed. Every failure returns ToolError. There is no tool that clears a hazard.
 */
export interface GuideTools {
  [k: string]: unknown | undefined;
}
/**
 * Search this appliance's manual pages for a query; returns page numbers and short snippets.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "LookupManualArgs".
 */
export interface LookupManualArgs {
  query: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "ManualHit".
 */
export interface ManualHit {
  page: number;
  snippet: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "LookupManualResult".
 */
export interface LookupManualResult {
  hits: ManualHit[];
}
/**
 * Find a card part on a frame; Spark grounds it and returns a box on the 0-1000 grid.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "LocateArgs".
 */
export interface LocateArgs {
  part_id: string;
  frame_id: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "LocateResult".
 */
export interface LocateResult {
  found: boolean;
  box: null | Box;
  frame_id: string;
  note?: string;
}
/**
 * Outline one part with SAM 3.1: by card part_id or by a short noun phrase, optionally from a box.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "SegmentArgs".
 */
export interface SegmentArgs {
  part_id: string | null;
  text: string | null;
  frame_id: string;
  box: null | Box;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "Mask".
 */
export interface Mask {
  found: boolean;
  count: number;
  /**
   * SVG path in frame pixels
   */
  mask_svg: string | null;
  box: null | Box;
  conf: number | null;
  area_pct?: number;
  frame_id: string;
  cached?: boolean;
}
/**
 * Cite a card step by step_id, or the manual by page and its exact words.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "CiteArgs".
 */
export interface CiteArgs {
  step_id: string | null;
  page: number | null;
  quote: string | null;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "Citation".
 */
export interface Citation {
  step_id: string;
  verified: boolean;
  page: number;
  quote: string;
  reason?: string;
}
/**
 * Ask the independent checker whether the step is visibly done. Async: the verdict arrives as an event. The guide can never mark a step done itself.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "CheckStepArgs".
 */
export interface CheckStepArgs {
  step_id: string;
}
/**
 * Immediate result; the verdict follows (judge.schema.json Verdict)
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "CheckStepResult".
 */
export interface CheckStepResult {
  step_id: string;
  pending: boolean;
  check_id?: string;
}
/**
 * Report a hazard. It can only stop or pause the repair, never resume it.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "ReportHazardArgs".
 */
export interface ReportHazardArgs {
  /**
   * null when no rule fits; code then picks the family's STOP
   */
  rule_id: null | RuleId;
  evidence: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "ReportHazardResult".
 */
export interface ReportHazardResult {
  accepted: boolean;
  state: "HOLD" | "STOP";
}
/**
 * Ask the user for a different view.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "AskForViewArgs".
 */
export interface AskForViewArgs {
  kind: "closer" | "side" | "light";
  reason: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "AskForViewResult".
 */
export interface AskForViewResult {
  new_frame: boolean;
  waited_s: number;
  frame_id?: string | null;
}
/**
 * Read-only state of a household appliance from Home Assistant, by the household's alias.
 *
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "HaStateArgs".
 */
export interface HaStateArgs {
  alias: string;
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "HaStateResult".
 */
export interface HaStateResult {
  alias: string;
  available: boolean;
  entities: {
    entity_id: string;
    state: string;
    unit?: string;
    last_changed?: string;
  }[];
}
/**
 * This interface was referenced by `GuideTools`'s JSON-Schema
 * via the `definition` "ToolError".
 */
export interface ToolError {
  error: string;
  retry: boolean;
}
