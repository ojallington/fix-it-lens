/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * One line per answer to the post-session feedback form (eval/consent/feedback.md, which names each question's key), appended to FIXIT_PRIVATE/feedback/feedback.jsonl by the server after a session in the app, or by `python -m eval.feedback import` from a filled PDF. Only a household in the store's pilot_cohort view writes one (consented, not deleted, not our own or a test, joined by a pilot invite). Written answers are stored exactly as typed, never trimmed or rewritten by the server. A deleted household's records go with it.
 */
export type FeedbackRecord = {
  [k: string]: unknown | undefined;
} & {
  id: string;
  /**
   * households.id
   */
  household: string;
  at: string;
  source: "app" | "pdf";
  lang: "nl" | "en" | null;
  /**
   * The form these answers' keys belong to
   */
  form_version: 1;
  /**
   * null is no answer. Tick boxes take their box's value; written answers are as typed
   */
  answers: {
    would_use: "yes" | "maybe" | "no" | null;
    /**
     * As typed; longer than 2,000 characters is refused (422), never cut
     */
    would_use_why: string | null;
    practical: "yes" | "partly" | "no" | null;
    /**
     * As typed; longer than 2,000 characters is refused (422), never cut
     */
    hard: string | null;
    unsafe: "no" | "yes" | null;
    /**
     * As typed; longer than 2,000 characters is refused (422), never cut
     */
    unsafe_which: string | null;
    /**
     * As typed; longer than 2,000 characters is refused (422), never cut
     */
    monthly: string | null;
    /**
     * As typed; longer than 2,000 characters is refused (422), never cut
     */
    friend: string | null;
  };
  /**
   * none: the answers count in the totals and are never printed
   */
  quote: "none" | "anonymous" | "named";
  /**
   * The name to quote them by: set with quote named, null otherwise. Withdrawing blanks it, so a withdrawn named quote may have none
   */
  quote_name: string | null;
  /**
   * Set when the household withdrew: its words are never printed again
   */
  withdrawn_at: string | null;
};
