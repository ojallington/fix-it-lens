/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "Lang".
 */
export type Lang = string;
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "Sha256".
 */
export type Sha256 = string;

/**
 * A country file: rules/<country>.yaml. In words: schema/rules.md. Rule IDs are <country>.<family>.<zone>. A country with no file fails closed.
 */
export interface CountryRules {
  /**
   * ISO 3166-1 alpha-2, lower case
   */
  country: string;
  version: string;
  /**
   * Answer languages this file has a rule table for
   *
   * @minItems 1
   */
  languages: [Lang, ...Lang[]];
  emergency: LangText;
  allow_lists: AllowLists;
  /**
   * @minItems 1
   */
  rules: [Rule, ...Rule[]];
}
/**
 * The stop script for an emergency, per language
 */
export interface LangText {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "AllowLists".
 */
export interface AllowLists {
  /**
   * The manufacturer's user-serviceable actions; everything else on a sealed boiler is a STOP
   */
  sealed_boiler: AllowedAction[];
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "AllowedAction".
 */
export interface AllowedAction {
  /**
   * e.g. read_pressure, top_up, bleed_radiator, reset
   */
  id: string;
  names: LangText1;
  /**
   * @minItems 1
   */
  sources: [Source, ...Source[]];
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "LangText".
 */
export interface LangText1 {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "Source".
 */
export interface Source {
  title: string;
  url?: string;
  section?: string;
  page?: number;
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "Rule".
 */
export interface Rule {
  id: string;
  family: "gas" | "mains" | "water" | "heat" | "pressure" | "co" | "chemical" | "mechanical";
  zone: string;
  parts: LangList;
  verbs: LangList1;
  action: "STOP" | "HOLD" | "ALLOW";
  line: LangText2;
  script: LangText3;
  /**
   * Zone ring; 3 cm is a floor, enforced again in code
   */
  ring_cm: number;
  landlord: boolean;
  /**
   * @minItems 1
   */
  sources: [Source, ...Source[]];
  /**
   * sha256 of card lines that name the part only to look at it
   */
  look_only_exemptions: Sha256[];
}
/**
 * Part names per language; any red-zone part name in a sentence is held whatever the verb
 */
export interface LangList {
  [k: string]: string[] | undefined;
}
/**
 * Action verbs per language; part x verb is the sentence filter's match
 */
export interface LangList1 {
  [k: string]: string[] | undefined;
}
/**
 * The fixed line the app says in place of a held sentence
 */
export interface LangText2 {
  [k: string]: string | undefined;
}
/**
 * The stop script, said when the rule stops the repair
 */
export interface LangText3 {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `CountryRules`'s JSON-Schema
 * via the `definition` "LangList".
 */
export interface LangList2 {
  [k: string]: string[] | undefined;
}
