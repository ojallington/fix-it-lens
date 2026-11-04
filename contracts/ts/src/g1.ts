/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * The G1 constants and test vectors (rules/g1.json). rules/ owns the values. Python and TypeScript run the same vectors.
 */
export interface G1File {
  version: string;
  constants: G1Constants;
  vectors: G1Vector[];
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "G1Constants".
 */
export interface G1Constants {
  /**
   * Stop when time to contact drops under this (400)
   */
  ttc_ms: number;
  /**
   * Stop when a fingertip comes within this (20), unless the step has a ring
   */
  dist_cm: number;
  /**
   * A hand lost within this gap of a red zone pauses (40)
   */
  near_cm: number;
  /**
   * No zone shrinks below this (3)
   */
  ring_floor_cm: number;
  /**
   * An older frame pauses (200)
   */
  frame_max_age_ms: number;
  /**
   * The stop path's p95 budget (150)
   */
  stop_p95_ms: number;
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "G1Vector".
 */
export interface G1Vector {
  id: string;
  description: string;
  /**
   * @minItems 1
   */
  frames: [G1Frame, ...G1Frame[]];
  expect: G1Expect;
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "G1Frame".
 */
export interface G1Frame {
  t_ms: number;
  frame_age_ms?: number;
  /**
   * Empty means the tracker lost the hand
   */
  fingertips: Point[];
  zone: Zone;
  tool_len_cm: number;
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "Point".
 */
export interface Point {
  x: number;
  y: number;
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "Zone".
 */
export interface Zone {
  /**
   * Image pixels
   *
   * @minItems 3
   */
  polygon: [Point, Point, Point, ...Point[]];
  ring_cm: number;
  /**
   * On a ring step the distance trigger gives way; TTC runs against the ring alone
   */
  ring_step?: boolean;
  scale_cm_per_px: number;
}
/**
 * This interface was referenced by `G1File`'s JSON-Schema
 * via the `definition` "G1Expect".
 */
export interface G1Expect {
  /**
   * null = none
   */
  stop_at_t_ms: number | null;
  kind: "stop" | "pause" | null;
}
