/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * One line per model call in FIXIT_PRIVATE/ledger.jsonl: the fields of data/spend.jsonl plus lane, household, session_id and mode. Writers hold an exclusive flock for read-check-append.
 */
export interface LedgerLine {
  ts: string;
  run?: string | null;
  /**
   * Legacy probe label; the build writes role here
   */
  probe?: string | null;
  note?: string | null;
  role?: string | null;
  call_id?: string | null;
  request_sha256?: string | null;
  model: string;
  response_model: string | null;
  status: string;
  incomplete?: string | null;
  store: false;
  latency_s?: number | null;
  ttft_s?: number | null;
  first_event_s?: number | null;
  input_tokens?: number | null;
  cached_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  images_processed?: number | null;
  video_frames_processed?: number | null;
  audio_seconds?: number | null;
  usd: number;
  error: string | null;
  lane: string;
  household: string | null;
  session_id: string | null;
  mode: "api" | "local" | "partner";
  /**
   * Who served the call. Absent or null reads as meta (every line before this field).
   */
  provider?: "meta" | "baseten" | "fireworks" | "modal" | "local" | null;
  /**
   * Which public way in the household came by: door (the open door) or judges (a judges' link). Absent or null: not a public session (an invite, our own or a test home). Public sub-budgets count by it.
   */
  origin?: "door" | "judges" | null;
}
