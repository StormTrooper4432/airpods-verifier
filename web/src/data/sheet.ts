/**
 * The sheet's vocabulary, mirrored from the Python pipeline.
 *
 * Rule parameters, weights, tiers, sources and panel bindings are copies of
 * `rules_engine.RULES`; panel descriptions are copies of the `panels` block in
 * `knowledge/airpods_pro_2.yaml`, each with the provenance the knowledge base
 * carries. Nothing here invents a specification. `tests/test_web_contract.py`
 * fails if this file and the Python drift apart.
 */

export type PanelId =
  | 'box_front'
  | 'box_back'
  | 'box_bottom_label'
  | 'box_side'
  | 'box_top'
  | 'case_exterior'
  | 'case_lid_underside'
  | 'earbud'
  | 'accessories'
  | 'other';

export type Status = 'PASS' | 'FAIL' | 'UNKNOWN';
export type Verdict = 'GENUINE' | 'HIGH_RISK_SUSPECTED' | 'INCONCLUSIVE';

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Panel {
  id: PanelId;
  /** Name the record uses for it. */
  label: string;
  /** Shorter form when the panel is too narrow for the full name. */
  plotLabel?: string;
  /** How the sheet names it in a sentence. */
  phrase: string;
  /** Where it sits when the carton is laid flat, or on the hardware row. */
  boxes: Box[];
  group: 'carton' | 'hardware';
  /** What is printed there. Only stated where the knowledge base sources it. */
  carries?: string;
  /** How to shoot it, when shooting it wrong is why a check never runs. */
  capture?: string;
  /** The same fact at plot scale. */
  short?: string;
  carriesSource?: string;
  /** Drawn figure for hardware panels; carton panels are plain rectangles. */
  figure?: 'case' | 'lid' | 'bud' | 'tips';
}

/**
 * The unfolded carton, at the scale it is drawn. Cropped close to the drawing:
 * dead margin costs nothing on a desktop and costs the whole diagram on a phone.
 */
export const PLOT_VIEWBOX = '80 30 800 716';

export const PANELS: Panel[] = [
  {
    id: 'box_top',
    label: 'Top',
    phrase: 'the box top',
    group: 'carton',
    boxes: [{ x: 200, y: 52, w: 280, h: 88 }],
  },
  {
    id: 'box_side',
    label: 'Sides',
    phrase: 'a box side',
    group: 'carton',
    boxes: [
      { x: 100, y: 140, w: 100, h: 280 },
      { x: 480, y: 140, w: 100, h: 280 },
    ],
  },
  {
    id: 'box_front',
    label: 'Front',
    phrase: 'the box front',
    group: 'carton',
    boxes: [{ x: 200, y: 140, w: 280, h: 280 }],
  },
  {
    id: 'box_back',
    label: 'Back',
    phrase: 'the box back',
    group: 'carton',
    boxes: [{ x: 580, y: 140, w: 280, h: 280 }],
    carries: 'Multilingual product description, copyright line, regulatory marks.',
    short: 'copyright · regulatory marks',
    carriesSource: 'r/AreMyAirpodsAuthentic community guide',
  },
  {
    id: 'box_bottom_label',
    label: 'Underside label',
    plotLabel: 'Underside label',
    phrase: 'the underside label',
    group: 'carton',
    boxes: [{ x: 200, y: 420, w: 280, h: 88 }],
    carries: 'Part number, model numbers, UPC and serial.',
    short: 'part no. · model no. · serial',
    carriesSource: 'r/AreMyAirpodsAuthentic community guide',
  },
  {
    id: 'case_exterior',
    label: 'Case, exterior',
    plotLabel: 'Case',
    phrase: 'the case exterior',
    group: 'hardware',
    figure: 'case',
    boxes: [{ x: 100, y: 568, w: 172, h: 156 }],
  },
  {
    id: 'case_lid_underside',
    label: 'Case lid, underside',
    plotLabel: 'Lid, inside',
    phrase: 'the underside of the case lid',
    group: 'hardware',
    figure: 'lid',
    boxes: [{ x: 296, y: 568, w: 172, h: 156 }],
    carries: 'The serial, printed on the underside of the lid.',
    /* Measured: a lid filling the frame reads correctly at the shipped
       downscale ceiling; the same lid at arm's length does not read at all.
       That is why the case lid serial is 0 of 20 on the corpus. See the
       case-lid note in knowledge/airpods_pro_2.yaml. */
    capture: 'fill the frame with it — the serial is about a millimetre',
    short: 'serial',
    carriesSource: "Apple, 'Identify your AirPods'",
  },
  {
    id: 'earbud',
    label: 'Earbud stem',
    plotLabel: 'Earbud',
    phrase: 'an earbud stem',
    group: 'hardware',
    figure: 'bud',
    boxes: [{ x: 492, y: 568, w: 172, h: 156 }],
    carries: 'Each earbud carries its own serial, different from the case serial.',
    /* Smaller print than the lid's, so the framing rule applies harder still. */
    capture: 'as close as the camera will focus — this print is smaller still',
    short: 'serial',
    carriesSource: 'r/AreMyAirpodsAuthentic community guide',
  },
  {
    id: 'accessories',
    label: 'Accessories',
    phrase: 'the accessories',
    group: 'hardware',
    figure: 'tips',
    boxes: [{ x: 688, y: 568, w: 172, h: 156 }],
  },
];

export const PANEL_BY_ID: Record<string, Panel> = Object.fromEntries(
  PANELS.map((p) => [p.id, p]),
);

export interface Rule {
  tier: 1 | 2;
  parameter: string;
  weight: number;
  disqualifying: boolean;
  informational?: true;
  source: string;
  answerableFrom: PanelId[];
  /** All of these must be photographed before the check can run at all. */
  requiresPanels?: PanelId[];
}

/** In the order the ledger prints them: most decisive first. */
export const RULES: Rule[] = [
  {
    tier: 1,
    parameter: 'Model Number Compatibility',
    weight: 5.0,
    disqualifying: true,
    source: "Apple, 'Identify your AirPods' (support.apple.com)",
    answerableFrom: ['box_bottom_label', 'case_lid_underside', 'earbud'],
  },
  {
    tier: 2,
    parameter: 'Packaging Seal Type',
    weight: 3.0,
    disqualifying: true,
    source: 'r/AreMyAirpodsAuthentic community guide, Step 1 (ruled authoritative by owner)',
    answerableFrom: ['box_front', 'box_back', 'box_side', 'box_top'],
  },
  {
    tier: 2,
    parameter: 'Copyright Year Constraint',
    weight: 2.0,
    disqualifying: true,
    source: "Apple 'Identify your AirPods' (introduction years); owner (2025 ceiling)",
    answerableFrom: ['box_back', 'box_bottom_label'],
  },
  {
    tier: 2,
    parameter: 'Serial Number Format',
    weight: 1.5,
    disqualifying: false,
    source:
      'Observed on 4 genuine units, 7 corpus listings and 2 counterfeits: 10 characters ' +
      'throughout, except AirPods Pro 1 at 12. Well-formedness only - both counterfeits ' +
      'used 10 characters too, so length does not discriminate',
    answerableFrom: ['box_bottom_label', 'case_lid_underside', 'earbud'],
  },
  {
    tier: 2,
    parameter: 'Serial Cross-Match',
    weight: 4.0,
    disqualifying: true,
    source:
      'r/AreMyAirpodsAuthentic community guide, Step 2 (four serial locations); ' +
      "Apple 'Identify your AirPods' (serial is on the underside of the case lid)",
    answerableFrom: ['box_bottom_label', 'case_lid_underside', 'earbud'],
    requiresPanels: ['box_bottom_label', 'case_lid_underside'],
  },
  {
    tier: 2,
    parameter: 'Serial Label Format',
    weight: 2.5,
    disqualifying: true,
    source: 'Project owner (domain expert); corroborated by community-guide label photographs',
    answerableFrom: ['box_bottom_label'],
  },
  {
    tier: 2,
    parameter: 'Label Typography',
    weight: 0.0,
    disqualifying: false,
    informational: true,
    source:
      "Measured against San Francisco (Apple's packaging typeface, shipped with macOS). " +
      'NOT CALIBRATED - reported as an observation, never as a finding',
    answerableFrom: ['box_bottom_label'],
  },
  {
    tier: 2,
    parameter: 'Typography & Print Quality',
    weight: 1.5,
    disqualifying: false,
    source: 'Community guide (printed misspellings); corpus measurement (kerning excluded)',
    answerableFrom: ['box_back', 'box_front', 'box_bottom_label'],
  },
  {
    tier: 2,
    parameter: 'Photographed Surfaces',
    weight: 0.0,
    disqualifying: false,
    informational: true,
    source: 'Observation only - no factual claim made',
    answerableFrom: [],
  },
];

export const RULE_BY_PARAMETER: Record<string, Rule> = Object.fromEntries(
  RULES.map((r) => [r.parameter, r]),
);

/** Sum of the weights that actually vote. Mirrors rules_engine. */
export const TOTAL_WEIGHT = RULES.reduce((n, r) => n + r.weight, 0);

/** rules_engine.MIN_COVERAGE_FOR_VERDICT */
export const MIN_COVERAGE_FOR_VERDICT = 0.6;

export interface EvidenceItem {
  tier: number;
  parameter: string;
  status: Status;
  detail: string;
  source: string;
}

/** One ledger row, positioned on the plot. */
export interface Finding {
  n: number;
  parameter: string;
  status: Status;
  panel: PanelId | null;
}

export interface Report {
  summary_verdict: Verdict;
  confidence_score: number;
  evidence_ledger: EvidenceItem[];
  next_steps: string[];
}

export interface Specimen {
  id: string;
  title: string;
  standfirst: string;
  panels: PanelId[];
  variant: string;
  report: Report;
}

export const VERDICT_GRADE: Record<Verdict, string> = {
  GENUINE: 'No disqualifying finding',
  HIGH_RISK_SUSPECTED: 'Disqualifying finding',
  INCONCLUSIVE: 'Insufficient evidence',
};

/**
 * The panel a finding is plotted on: the first surface the rule can be read
 * from that was actually photographed.
 *
 * Returns null when none of them was. A mark drawn on an unexposed panel would
 * claim the check was read off a surface nobody sent — a green tick sitting on
 * a blank panel is exactly the false reassurance this tool exists to refuse.
 * The check still prints in the ledger; it just has no place on the plot yet.
 */
export function plotPanel(rule: Rule | undefined, shown: PanelId[]): PanelId | null {
  if (!rule || rule.answerableFrom.length === 0) return null;
  return rule.answerableFrom.find((p) => shown.includes(p)) ?? null;
}

/** What a check is still waiting for, stated from the rule's own bindings. */
export function awaitingDetail(rule: Rule): string {
  if (rule.answerableFrom.length === 0) {
    return 'Records which surfaces the photographs actually show. Nothing photographed yet.';
  }
  const names = rule.answerableFrom.map((p) => PANEL_BY_ID[p]?.phrase ?? p);
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}`;
  if (rule.requiresPanels?.length) {
    const both = rule.requiresPanels.map((p) => PANEL_BY_ID[p]?.phrase ?? p);
    return `Needs both ${both.join(' and ')} in the same set. Not yet examined.`;
  }
  return `Reads from ${list}. Not yet examined.`;
}

/** The unstruck record: every check present, none of them run. */
export function blankLedger(): EvidenceItem[] {
  return RULES.map((rule) => ({
    tier: rule.tier,
    parameter: rule.parameter,
    status: 'UNKNOWN' as const,
    detail: awaitingDetail(rule),
    source: rule.source,
  }));
}

/**
 * The evidence weight a set of photographs can actually reach.
 *
 * Measured on the labelled corpus: the surfaces people photograph by instinct
 * — box front, case, earbuds — top out at 11.0 of 19.5, which is 56% against a
 * 60% floor. Those examinations were unanswerable before the model saw them,
 * and no rule or prompt change moves that number. Only another photograph does.
 *
 * Mirrors the ceiling `rules_engine` applies; `test_web_contract.py` fails if
 * the two disagree.
 */
export function reachableWeight(panels: PanelId[]): number {
  const shown = new Set(panels);
  return RULES.filter((rule) => {
    if (rule.weight === 0) return false;
    if (rule.requiresPanels && !rule.requiresPanels.every((p) => shown.has(p))) return false;
    if (rule.answerableFrom.length && !rule.answerableFrom.some((p) => shown.has(p))) return false;
    return true;
  }).reduce((total, rule) => total + rule.weight, 0);
}

/** Whether this set could reach any verdict at all, before it is even sent. */
export function canReachVerdict(panels: PanelId[]): boolean {
  return reachableWeight(panels) / TOTAL_WEIGHT >= MIN_COVERAGE_FOR_VERDICT;
}

/**
 * The surface that would add the most evidence weight right now.
 *
 * Not a fixed list: which photograph matters most depends on what has already
 * been taken, so this is recomputed as the plot fills. Ranking by weight alone
 * would keep pointing at the underside label after it had been supplied.
 */
/**
 * Panels that can settle the pull-tab question in BOTH directions.
 *
 * Mirrors `_PANELS_SHOWING_AN_EDGE` in rules_engine.py; test_web_contract.py
 * fails if the two drift. The tear strip runs down the side, so box_side can
 * confirm a tab and can also establish there is none. box_top can only ever
 * confirm one is present — the engine refuses to read absence from it, because
 * the extractor has labelled an opened box's interior tray box_top and that
 * manufactures an edge view which does not exist.
 */
export const PANELS_SHOWING_AN_EDGE: PanelId[] = ['box_side'];

export function mostValuablePanel(shown: PanelId[]): { panel: PanelId; gain: number } | null {
  const base = reachableWeight(shown);
  const decisive = (panel: PanelId) => PANELS_SHOWING_AN_EDGE.includes(panel);
  let best: { panel: PanelId; gain: number } | null = null;
  for (const panel of PANELS) {
    if (shown.includes(panel.id)) continue;
    const gain = reachableWeight([...shown, panel.id]) - base;
    if (gain <= 0) continue;
    /* Ties were broken by array order, which put box_top ahead of box_side for
       the same promised 3.0 — and box_top is the one panel the engine will not
       read a missing tab from. Identical forecast, strictly worse photograph.
       On a tie, send them to the surface that can resolve it either way. */
    const better =
      best === null ||
      gain > best.gain ||
      (gain === best.gain && decisive(panel.id) && !decisive(best.panel));
    if (better) best = { panel: panel.id, gain };
  }
  return best;
}
