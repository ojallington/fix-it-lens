/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Manual".
 */
export type Manual = {
  [k: string]: unknown | undefined;
} & {
  /**
   * maker_site: the PDF's https URL on the maker's site. household_upload: urn:fixit:upload:<pdf_sha256>, never a local path
   */
  source_url: string;
  title?: string;
  /**
   * The maker's document number, if printed
   */
  doc_id?: string;
  lang: Lang;
  page_count: number;
  pdf_sha256?: Sha256;
  /**
   * sha256 of each page's extracted text, in page order (index 0 = page 1)
   */
  page_text_sha256: Sha256[];
  /**
   * Where the PDF came from: the maker's public support site, fetched on a user's request, or a household's own upload. A household_upload card is never shared. Absent on cards built before 1.23, which were all fetched from the maker's site
   */
  source?: "maker_site" | "household_upload";
  /**
   * The model codes the manual itself prints, as printed, including its own placeholder patterns ('WMN...', 'SMV4HA*'); code checks each against models_page at ingest. A plate's model links to this card only when it matches one of them after the plate read's normalisation. Empty: the manual prints none, and the card stays private to the home that built it. Absent on cards built before 1.23
   *
   * @maxItems 60
   */
  models?: string[];
  /**
   * The page that lists the models; required when models is not empty
   */
  models_page?: number;
};
/**
 * ISO 639-1
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Lang".
 */
export type Lang = string;
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Sha256".
 */
export type Sha256 = string;
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Slug".
 */
export type Slug = string;
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "RuleId".
 */
export type RuleId = string;
/**
 * A code as the manual spells it, e.g. E:32-00 or E24
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "FaultCode".
 */
export type FaultCode = string;
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Step".
 */
export type Step = {
  [k: string]: unknown | undefined;
} & {
  id: Slug;
  text: LangText;
  /**
   * manual (absent means manual): the maker's step, cited. fixit: our own step, only to undo what an earlier power-off step of the same job left undone, which the manual never undoes: plug it back in, switch it back on, turn the water back on, or put a part it took out back in place. A person writes it in a reviewed card, or ingest appends a fixed one (added_by); never a model. The phone marks it as ours
   */
  origin?: "manual" | "fixit";
  /**
   * null only on a fixit step
   */
  page: number | null;
  /**
   * null only on a fixit step
   */
  quote: Quote | null;
  done_when: DoneWhen[];
  parts: Slug[];
  power: "on" | "off" | "any";
  /**
   * red_zones ids active on this step
   */
  zones: Slug[];
  zone_rings?: ZoneRing[];
  view: "front" | "side";
  /**
   * Card lines that name a red-zone part only to look at it; their hashes are G0 exemptions
   */
  look_only_lines: string[];
  /**
   * Omitted or null: no visible part to witness; the judge and code decide alone
   */
  witness?: Witness | null;
  /**
   * fixit only: the earlier power-off step of this job whose plug, switch, water or part this step puts back
   */
  restores?: string;
  /**
   * Only on a fixit step: who added it; absent means person. person wrote it in a reviewed card; ingest is code that appends a fixed restore step
   */
  added_by?: "person" | "ingest";
  /**
   * What the camera does on this step (absent means check). check: the step check confirms it done on camera. find: the camera helps locate the step's parts (outlines), no done check; the person confirms. none: no camera, the person confirms. Never none on a step with zones, zone rings, a red-zone part or power on or off; find needs at least one part. The mode never changes a safety gate: a power-off step still waits for the plug seen out
   */
  camera?: "check" | "find" | "none";
};
/**
 * One row of the manual's fault, indicator or symptom table. Its quote is the table row (code, cause and remedy cells together) and passes the citation check like a step; meaning, cause and remedy are written from that quote only, in en plus the manual's language, and go through G0 at load. A row whose quote fails is dropped.
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "TroubleshootingRow".
 */
export type TroubleshootingRow = {
  [k: string]: unknown | undefined;
} & TroubleshootingRow1 & {
    id: Slug;
    /**
     * [] for a light or a symptom
     */
    codes: FaultCode[];
    /**
     * e.g. 'the tap symbol lights up'
     */
    indicator: null | LangText;
    /**
     * Where the manual draws the indicator; null when there is no figure
     */
    symbol: null | {
      page: number;
      /**
       * [x0, y0, x1, y1] as fractions of the page, origin top left, so the phone can crop any render of the page. Found by code (the drawing nearest the row's quote on that page), never by a model.
       *
       * @minItems 4
       * @maxItems 4
       */
      bbox: [number, number, number, number];
    };
    symptom: null | LangText;
    meaning: LangText;
    cause: null | LangText;
    remedy: LangText2;
    /**
     * Who acts, as the manual says. 'landlord' is never on a card: the server derives it from the household and the rules file.
     */
    remedy_kind: "user" | "technician" | "installer";
    /**
     * 'Do X; if it persists, call the service': only with remedy_kind user
     */
    escalate_to: "technician" | "installer" | null;
    /**
     * The kept fix job that carries out a user remedy; null when that job was dropped (the row stays, its remedy cited)
     */
    job_ref: null | Slug;
    /**
     * Upkeep jobs the manual ties to this fault, each tie cited
     */
    prevented_by: {
      job_id: Slug;
      quote_ref: Slug;
    }[];
    /**
     * Only when the manual says it, e.g. a third top-up in a year: call the installer
     */
    repeat: null | {
      count: number;
      days: number;
      quote_ref: Slug;
    };
    page: number;
    quote_ref: Slug;
  };
export type TroubleshootingRow1 =
  | {
      codes?: {
        [k: string]: unknown | undefined;
      };
    }
  | {
      indicator?: LangText;
    }
  | {
      symptom?: LangText;
    };

/**
 * A checked card: cards/<brand>/<model>.yaml. It cites manual pages with short verbatim quotes and page-text hashes; it never holds the manual's text. In words: schema/card.md.
 */
export interface Card {
  schema_version: 1;
  /**
   * Lower-case maker slug, e.g. bosch
   */
  brand: string;
  /**
   * The public model code, as on the plate (never the serial)
   */
  model: string;
  aliases: string[];
  appliance_kind?: "dishwasher" | "washer" | "dryer" | "boiler" | "fridge" | "oven" | "hob" | "hood" | "other";
  /**
   * The model ID that built the appliance file, e.g. muse-wonder-1.0
   */
  appliance_file_model: string;
  manual: Manual;
  parts: Part[];
  red_zones: RedZone[];
  /**
   * Quotes referred to by quote_ref
   */
  quotes: RefQuote[];
  /**
   * @minItems 1
   */
  jobs: [Job, ...Job[]];
  /**
   * The manual's fault, indicator and symptom table; absent on cards built before it
   */
  troubleshooting?: TroubleshootingRow[];
  /**
   * Jobs refused at build, by reason; absent on cards built before 1.23
   */
  refused?: RefusedJob[];
  /**
   * Words that bias speech recognition
   */
  keywords: string[];
  /**
   * Filled by the card loader at load time; absent on disk
   */
  checks?: null | Checks;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Part".
 */
export interface Part {
  id: Slug;
  names: LangText;
  /**
   * How Spark is asked to find it: by what it does, or by how it looks
   */
  locate: "function" | "appearance";
  /**
   * What to look for, e.g. 'round grey cylinder with a twist handle, bottom centre'
   */
  cue: string;
  /**
   * A short noun phrase for SAM (one concept, no verbs or 'and')
   */
  sam_text: string;
  kind: "allowed" | "red_zone";
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "LangText".
 */
export interface LangText {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "RedZone".
 */
export interface RedZone {
  id: Slug;
  part_id: Slug;
  rule_id: RuleId;
  page: number;
  quote: Quote;
  /**
   * 3 cm is a floor, enforced again in code
   */
  ring_cm: number;
}
/**
 * Verbatim from the manual, in the manual's language, at most 2 sentences and at least 20 characters
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Quote".
 */
export interface Quote {
  text: string;
  lang: Lang;
  page: number;
  /**
   * An English translation of text, shown in grey under it. Never citation-checked, never replaces text; it is manual text on screen, so it goes through G0 like step text.
   */
  en?: string | null;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "RefQuote".
 */
export interface RefQuote {
  id: Slug;
  text: string;
  lang: Lang;
  page: number;
  /**
   * An English translation of text, shown in grey under it. Never citation-checked, never replaces text; it is manual text on screen, so it goes through G0 like step text.
   */
  en?: string | null;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Job".
 */
export interface Job {
  id: Slug;
  title: LangText;
  kind: "upkeep" | "fix";
  interval: null | Interval;
  fault_codes: FaultCode[];
  /**
   * Default for renters; the country rules file can override
   */
  landlord: boolean;
  duration_min: null | Duration;
  /**
   * @minItems 1
   */
  steps: [Step, ...Step[]];
  /**
   * The short noun a reminder names ('filter'); absent: derived from the title
   */
  due_name?: LangText | null;
  after?: After[] | null;
  /**
   * Optional: what the person needs ready before the job starts, each cited to the manual. Absent, null or []: the job starts with no list
   */
  needs?: Need[] | null;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Interval".
 */
export interface Interval {
  every: number;
  unit: "day" | "week" | "month" | "year" | "cycle";
  quote_ref: Slug;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Duration".
 */
export interface Duration {
  value: number;
  source: "manual_quote" | "session_log";
  /**
   * quote id (manual_quote) or session id (session_log)
   */
  ref?: string;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "DoneWhen".
 */
export interface DoneWhen {
  id: Slug;
  text: LangText;
  quote_ref: null | Slug;
}
/**
 * A step-level zone override (e.g. the boiler's 3 cm ring beside the fill valve); needs safety review
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "ZoneRing".
 */
export interface ZoneRing {
  zone_id: Slug;
  ring_cm: number;
}
/**
 * What the SAM witness checks for this step: the part present or absent in the NOW frame, near its anchor
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Witness".
 */
export interface Witness {
  /**
   * A part id of this card
   */
  part: string;
  expect: "present" | "absent";
  /**
   * The part it sits on or in (a drain cap's drain); null if none
   */
  anchor?: Slug | null;
}
/**
 * This job comes to mind early after job_id was done. It never moves a due date; it only adds the history clause to a reminder
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "After".
 */
export interface After {
  job_id: Slug;
  because: LangText;
  /**
   * A quotes[] id; the citation check covers it
   */
  quote_ref: string;
}
/**
 * One thing to have ready before a job starts (a product, a tool or a consumable), as the manual names it. Its quote (quote_ref, a quotes[] id) passes the citation check like a step's, so a card never invents a requirement; a need whose quote fails is dropped. Readiness is the person's word: it never clears a hold or a safety rule
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Need".
 */
export interface Need {
  /**
   * Unique within its job
   */
  id: string;
  what: LangText1;
  /**
   * Optional: product (a named cleaner or agent), tool (kept and reused: a screwdriver, a towel), consumable (used up: salt, a new filter)
   */
  kind?: "product" | "tool" | "consumable";
  /**
   * A quotes[] id: the manual's words that call for it, with their page; the citation check covers it
   */
  quote_ref: string;
}
/**
 * What to have ready, per language, in our short words from the quote ('machine cleaner'); it goes through G0 at load
 */
export interface LangText1 {
  [k: string]: string | undefined;
}
/**
 * The manual's remedy, e.g. 'call the service'
 */
export interface LangText2 {
  [k: string]: string | undefined;
}
/**
 * A job the manual describes that the rule engine refused at build, kept as a name and a page so the app can say who to call, never as steps; or (reason not_built) a procedure the build could not turn into steps, kept as the manual's heading and page so the app can point at it
 *
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "RefusedJob".
 */
export interface RefusedJob {
  id?: Slug;
  title?: LangText;
  reason: "gas" | "mains" | "needs_professional" | "other" | "not_built";
  page: number;
  /**
   * The procedure's heading as the manual prints it, in the manual's language; only with reason not_built
   */
  heading?: string;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "Checks".
 */
export interface Checks {
  citation: CheckResult;
  rules: CheckResult;
}
/**
 * This interface was referenced by `Card`'s JSON-Schema
 * via the `definition` "CheckResult".
 */
export interface CheckResult {
  status: "pass" | "fail" | "unchecked";
  checked_at?: string;
  failures: string[];
  rule_ids?: RuleId[];
}
