/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * SafetyState = OK | HOLD{precondition} | STOP{rule_id, evidence, script}, and the events that move it. The model may only raise; code clears, on its own rules. There is no event that turns a gate off.
 */
export type Safety = SafetyState | SafetyEvent;
export type SafetyState = Ok | Hold | Stop;
export type RuleId = string;
/**
 * One move of the safety state. source=model can only raise (to HOLD or STOP), never clear.
 */
export type SafetyEvent = {
  [k: string]: unknown | undefined;
} & {
  t_ms: number;
  source: "model" | "g0" | "g1" | "rules" | "ha" | "user" | "code";
  kind: "raise" | "clear";
  to: SafetyState;
  reason: string;
};

export interface Ok {
  state: "OK";
}
export interface Hold {
  state: "HOLD";
  precondition:
    "side_view" | "power_off_seen" | "hand_visible" | "fresh_frame" | "zone_outlined" | "view_scale" | "user_confirm";
  rule_id?: null | RuleId;
  detail?: string;
}
export interface Stop {
  state: "STOP";
  rule_id: RuleId;
  evidence: string;
  /**
   * The country's fixed stop script, in the answer language
   */
  script: string;
}
