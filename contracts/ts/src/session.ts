/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "Sha256".
 */
export type Sha256 = string;
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "Event".
 */
export type Event = ApiEvent | ClientEvent | StateEvent;

/**
 * The recording format. A session is FIXIT_PRIVATE/sessions/<id>/ with meta.json, events.jsonl, frames/<frame_id>.jpg and audio/<utterance_id>.wav. Request bodies go only to FIXIT_PRIVATE/requests/<sha>.json. Fixtures (fixtures/<lane>/<request_sha256>.jsonl) hold a FixtureHeader line then FixtureEvent lines: responses only.
 */
export interface SessionRecording {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "SessionMeta".
 */
export interface SessionMeta {
  session_id: string;
  household?: string | null;
  appliance_id?: string | null;
  /**
   * cards/<brand>/<model>.yaml
   */
  card?: string | null;
  job_id?: string | null;
  started_at: string;
  mode: "api" | "local" | "capture";
  lang: string;
  country?: string | null;
  contracts_version: string;
  commit?: string | null;
}
/**
 * dir c2s or s2c: the ws message itself (kind = its type), minus frame bytes (frames are files)
 *
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "ClientEvent".
 */
export interface ClientEvent {
  t_ms: number;
  dir: "c2s" | "s2c";
  kind: string;
  msg?: {};
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "StateEvent".
 */
export interface StateEvent {
  t_ms: number;
  dir: "state";
  kind: string;
  from?: string;
  to?: string;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "StreamLine".
 *
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "FixtureEvent".
 */
export interface StreamLine {
  /**
   * Offset from the request start
   */
  t_ms: number;
  /**
   * One SSE event as received (response.* types), with request echoes stripped
   */
  event: {};
}
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "ApiEvent".
 */
export interface ApiEvent {
  t_ms: number;
  dir: "api";
  kind: "respond" | "sam_image" | "sam_video" | "transcribe" | "count_tokens";
  call_id: string;
  role: string;
  request_sha256: Sha256;
  params: CallParams;
  model: string;
  response_model: string | null;
  stream: StreamLine[];
  latency_ms: number;
  ttft_ms?: number | null;
  usd: number;
  status: "completed" | "incomplete" | "failed" | "error" | "refused" | "cache_hit";
  error?: string | null;
  cache_hit?: boolean;
}
/**
 * Request parameters that are safe to log. Never input, instructions or tools.
 *
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "CallParams".
 */
export interface CallParams {
  model: string;
  store: false;
  stream?: boolean;
  effort?: "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | null;
  max_output_tokens?: number | null;
  format_name?: string | null;
  tool_names?: string[];
  n_images?: number;
  image_px?: number | null;
  prompt_cache_key?: string | null;
  mask_encoding?: "one_bit" | "lossless" | null;
  language_bias?: string[];
  attempt?: number;
  /**
   * reasoning.summary asked for (the guide asks for auto; the rail shows what comes back)
   */
  reasoning_summary?: "auto" | "concise" | "detailed" | null;
}
/**
 * This interface was referenced by `SessionRecording`'s JSON-Schema
 * via the `definition` "FixtureHeader".
 */
export interface FixtureHeader {
  fixture: 1;
  request_sha256: Sha256;
  kind: "respond" | "sam_image" | "sam_video" | "transcribe" | "count_tokens" | "upload";
  role: string;
  params: CallParams;
  recorded_at: string;
  response_model: string | null;
  latency_ms?: number | null;
  usd?: number | null;
  http_status?: number | null;
}
