/**
 * OpenTrustSeal TypeScript SDK
 *
 * Trust verification for AI agent commerce.
 *
 * Quick start:
 *   import { check } from '@opentrustseal/sdk';
 *   const result = await check('merchant.com');
 *   if (result.recommendation === 'DENY') throw new Error(result.reasoning);
 */

const DEFAULT_BASE_URL = 'https://api.opentrustseal.com';

export interface Signal {
  score: number;
  [key: string]: unknown;
}

export interface Signals {
  domainAge: Signal;
  ssl: Signal;
  dns: Signal;
  content: Signal;
  reputation: Signal;
  identity: Signal;
}

export interface Jurisdiction {
  country: string;
  legalFramework: string;
  crossBorderRisk: 'standard' | 'elevated' | 'unknown';
  disputeResolution: 'established' | 'limited' | 'unknown';
  kycAvailable: boolean;
  hasPublicRegistry: boolean;
}

export interface ChecklistItem {
  category: string;
  item: string;
  status: 'pass' | 'fail' | 'improve' | 'available';
  impact: 'high' | 'medium' | 'low';
  fix: string;
}

export interface ChecklistSummary {
  total: number;
  passing: number;
  failing: number;
  improvable: number;
}

export interface CheckResult {
  checkId: string;
  domain: string;
  trustScore: number;
  recommendation: 'PROCEED' | 'CAUTION' | 'DENY';
  confidence: 'high' | 'medium' | 'low';
  cautionReason: 'incomplete_evidence' | 'weak_signals' | 'new_domain' | 'infrastructure' | null;
  reasoning: string;
  scoringModel: string;
  siteCategory: 'consumer' | 'infrastructure' | 'api_service';
  brandTier: 'well_known' | 'scored';
  crawlability: 'ok' | 'blocked';
  flags: string[];
  signals: Signals;
  jurisdiction: Jurisdiction;
  checklist: ChecklistItem[];
  checklistSummary: ChecklistSummary;
  signature: string;
  signatureKeyId: string;
  issuer: string;
  checkedAt: string;
  expiresAt: string;

  /** Convenience: is the site safe to transact with? */
  isSafe: boolean;
  /** Convenience: should the agent apply caution? */
  isRisky: boolean;
  /** Convenience: should the agent refuse? */
  isBlocked: boolean;
  /** Convenience: are there critical security flags? */
  hasCriticalFlags: boolean;

  /**
   * Confidence-aware action recommendation. Combines recommendation,
   * confidence, and cautionReason into a single decision hint agents can
   * branch on without re-deriving the logic.
   */
  recommendedAction: AgentAction;

  /**
   * Human-readable companion to recommendedAction. Suitable for agent logs
   * and tool-call output. Safe to show end users.
   */
  actionMessage: string;
}

export type AgentAction =
  | 'refuse_critical'     // malware/phishing detected, never proceed
  | 'refuse'              // DENY tier
  | 'proceed'             // PROCEED tier, non-low confidence
  | 'confirm_low_value'   // CAUTION with low confidence: small-dollar OK, confirm larger
  | 'confirm_new_domain'  // CAUTION because the domain is under 1 year old
  | 'confirm_caution'     // CAUTION for other reasons
  | 'review';             // unexpected state, fall back to human review

export interface OTSClientOptions {
  apiKey?: string;
  baseUrl?: string;
  timeout?: number;
}

const ACTION_MESSAGES: Record<AgentAction, string> = {
  refuse_critical: 'DO NOT proceed. Critical safety flags detected.',
  refuse: 'Refuse this transaction.',
  proceed: 'Safe to proceed with this merchant.',
  confirm_low_value:
    'Evidence incomplete. Not necessarily bad. Low-dollar OK. Confirm larger amounts.',
  confirm_new_domain: 'New domain. Confirm with user before transacting.',
  confirm_caution: 'Proceed with caution. Confirm with user first.',
  review: 'Unexpected state. Fall back to human review.',
};

function computeRecommendedAction(data: any, hasCriticalFlags: boolean): AgentAction {
  if (hasCriticalFlags) return 'refuse_critical';
  if (data.recommendation === 'DENY') return 'refuse';
  if (data.recommendation === 'PROCEED') return 'proceed';
  if (data.recommendation === 'CAUTION') {
    if (data.confidence === 'low') return 'confirm_low_value';
    if (data.cautionReason === 'new_domain') return 'confirm_new_domain';
    return 'confirm_caution';
  }
  return 'review';
}

function enrichResult(data: any): CheckResult {
  const critical = new Set(['MALWARE_DETECTED', 'PHISHING_DETECTED', 'RECENTLY_COMPROMISED']);
  const flags: string[] = data.flags || [];
  const hasCriticalFlags = flags.some(f => critical.has(f));
  const recommendedAction = computeRecommendedAction(data, hasCriticalFlags);

  return {
    ...data,
    isSafe: data.recommendation === 'PROCEED',
    isRisky: data.recommendation === 'CAUTION',
    isBlocked: data.recommendation === 'DENY',
    hasCriticalFlags,
    recommendedAction,
    actionMessage: ACTION_MESSAGES[recommendedAction],
  };
}

export class OTSClient {
  private baseUrl: string;
  private headers: Record<string, string>;
  private timeout: number;

  constructor(options: OTSClientOptions = {}) {
    this.baseUrl = (options.baseUrl || DEFAULT_BASE_URL).replace(/\/$/, '');
    this.timeout = options.timeout || 30000;
    this.headers = { 'User-Agent': 'opentrustseal-js/0.1.0' };
    if (options.apiKey) {
      this.headers['Authorization'] = `Bearer ${options.apiKey}`;
    }
  }

  async check(domain: string, options?: { refresh?: boolean }): Promise<CheckResult> {
    const url = `${this.baseUrl}/v1/check/${encodeURIComponent(domain)}${options?.refresh ? '?refresh=true' : ''}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const resp = await fetch(url, {
        headers: this.headers,
        signal: controller.signal,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.message || err.detail?.message || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      return enrichResult(data);
    } finally {
      clearTimeout(timer);
    }
  }

  async checkMultiple(domains: string[]): Promise<CheckResult[]> {
    return Promise.all(domains.map(d => this.check(d)));
  }
}

/** Check a domain using the default client (free tier, no API key). */
export async function check(domain: string, options?: { refresh?: boolean }): Promise<CheckResult> {
  const client = new OTSClient();
  return client.check(domain, options);
}
