export type VastDbAccessContext = 'dashboard' | 'explore' | 'suggestions';

const CONTEXT_LABEL: Record<VastDbAccessContext, string> = {
  dashboard: 'dashboard statistics',
  explore: 'explore timeline',
  suggestions: 'search suggestions',
};

/** User-facing message when VastDB/backend is unreachable or tables are missing. */
export function friendlyVastDbAccessMessage(context: VastDbAccessContext): string {
  return `Could not load ${CONTEXT_LABEL[context]}. Check backend logs and VastDB access.`;
}

/** VastDB or gateway error. */
export function isLikelyVastDbOrGatewayError(err: unknown): boolean {
  const e = err as {
    status?: number;
    statusCode?: number;
    message?: string;
    error?: { detail?: string };
  };
  const status = e?.status ?? e?.statusCode;
  if (status === 0 || status === 502 || status === 503 || status === 504) {
    return true;
  }
  const text = `${e?.message ?? ''} ${e?.error?.detail ?? ''}`.toLowerCase();
  return (
    text.includes('http failure') ||
    text.includes('service temporarily unavailable') ||
    text.includes('vastdb') ||
    text.includes('could not read vastdb') ||
    text.includes('network error') ||
    text.includes('failed to load dashboard') ||
    text.includes('failed to load explore') ||
    text.includes('failed to load suggestions') ||
    text.includes('prompts table')
  );
}

export function resolveApiAccessWarning(
  err: unknown,
  context: VastDbAccessContext,
): string {
  if (isLikelyVastDbOrGatewayError(err)) {
    return friendlyVastDbAccessMessage(context);
  }
  const e = err as { error?: { detail?: string }; message?: string };
  const detail = e?.error?.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim();
  }
  if (typeof e?.message === 'string' && e.message.trim()) {
    return e.message.trim();
  }
  return friendlyVastDbAccessMessage(context);
}
