/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * One line of eval/claims.json: every number that reaches the README or the video. A measured value that misses its target is printed as measured.
 */
export interface Claim {
  id: string;
  value: number | string | null;
  unit: string;
  n: number | null;
  ci: null | [number, number];
  measured_at: string;
  commit: string;
  command: string;
  /**
   * eval/rows.toml row ids
   */
  rows: string[];
  readme_anchor: string | null;
  /**
   * the figure the design aimed for, e.g. "2 of 40"; the published number is value, whether or not it meets it
   */
  target?: null | (number | string);
  note?: string;
}
