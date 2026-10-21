const QUERY_STOP_WORDS = new Set([
  'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'to', 'of', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'as', 'into',
  'and', 'or', 'but', 'not', 'if', 'then', 'than', 'that', 'this', 'there',
  'it', 'its', 'i', 'you', 'he', 'she', 'we', 'they', 'my', 'your', 'his',
  'next', 'near', 'beside', 'behind', 'around', 'through', 'under', 'over',
  'who', 'what', 'when', 'where', 'which', 'how', 'do', 'does', 'did',
  'has', 'have', 'had', 'can', 'could', 'would', 'should', 'will', 'shall',
  'only', 'just', 'also', 'still', 'even', 'very', 'more', 'most', 'some', 'any',
]);

const QUERY_ACTION_WORDS = new Set([
  'standing', 'stand', 'sitting', 'sit', 'walking', 'walk', 'running', 'run',
  'moving', 'move', 'looking', 'look', 'holding', 'hold', 'wearing', 'wear',
  'talking', 'talk', 'playing', 'play', 'dancing', 'dance', 'jumping', 'jump',
  'lying', 'lie', 'lay', 'eating', 'eat', 'drinking', 'drink', 'driving', 'drive',
]);

export interface SegmentObjectSource {
  object_classes?: string | null;
  object_counts?: string | null;
  perception_ok?: boolean | null;
}

export interface ObjectChip {
  label: string;
  count?: number;
  display: string;
}

function tokenizeQuery(query: string): string[] {
  return (query.toLowerCase().match(/[a-z0-9]+(?:[-'][a-z0-9]+)*/g) ?? []).filter(Boolean);
}

function isSkippableToken(token: string, skip: Set<string>): boolean {
  return skip.has(token) || token.length < 2;
}

function buildContentPhrases(tokens: string[], skip: Set<string>): string[] {
  const phrases: string[] = [];
  let current: string[] = [];
  for (const token of tokens) {
    if (isSkippableToken(token, skip)) {
      if (current.length) {
        phrases.push(current.join(' '));
        current = [];
      }
      continue;
    }
    current.push(token);
  }
  if (current.length) phrases.push(current.join(' '));
  return phrases;
}

export function extractHighlightTerms(query: string): string[] {
  if (!query?.trim()) return [];

  const skip = new Set([...QUERY_STOP_WORDS, ...QUERY_ACTION_WORDS]);
  const phrases = buildContentPhrases(tokenizeQuery(query), skip);

  const terms = new Set<string>();
  for (const phrase of phrases) {
    terms.add(phrase);
    if (phrase.includes(' ')) {
      for (const part of phrase.split(' ')) {
        if (part.includes('-') || part.includes("'")) {
          terms.add(part);
        }
      }
    }
  }

  return Array.from(terms).sort((a, b) => b.length - a.length || a.localeCompare(b));
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function partToPattern(part: string): string {
  if (part.includes('-') || part.includes("'")) {
    return part.split(/[-']/).filter(Boolean).map(escapeRegex).join('[-\\s]?');
  }
  return escapeRegex(part);
}

export function buildTermPattern(term: string): RegExp {
  const parts = term.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) {
    return /$^/;
  }
  const body = parts.map(partToPattern).join('[-\\s]+');
  return new RegExp(`(?<!\\w)(${body})(?!\\w)`, 'gi');
}

export function termInText(term: string, text: string): boolean {
  if (!term || !text) return false;
  return buildTermPattern(term).test(text);
}

export function termMatchesLabel(term: string, label: string): boolean {
  return termInText(term.trim(), label.trim());
}

export function objectMatchesQuery(label: string, terms: string[]): boolean {
  return terms.some(term => termMatchesLabel(term, label));
}

function normalizeObjectLabel(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  const stripped = trimmed.replace(/^\d+\s+/, '').trim();
  return stripped || trimmed;
}

function dedupeObjectLabels(labels: string[]): string[] {
  const seen = new Set<string>();
  return labels.filter(label => {
    const key = label.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function parseObjectClassList(raw?: string | null): string[] {
  if (!raw?.trim()) return [];
  return raw.split(/[,;|]/).map(normalizeObjectLabel).filter(Boolean);
}

export function segmentDisplayCaption(segment: {
  reasoning_content?: string | null;
}): string {
  const reasoning = segment.reasoning_content?.trim();
  if (reasoning) {
    const firstLine = reasoning.split('\n')[0]?.trim();
    if (firstLine) return firstLine;
  }
  return '';
}

export function parseStructuredObjects(segment: SegmentObjectSource): string[] {
  return dedupeObjectLabels(parseObjectClassList(segment.object_classes));
}

function parseObjectCounts(raw?: string | null): Record<string, number> {
  if (!raw?.trim()) return {};
  try {
    const data = JSON.parse(raw) as unknown;
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
      const label = normalizeObjectLabel(String(key || ''));
      const n = typeof value === 'number' ? value : Number(value);
      if (label && Number.isFinite(n) && n > 0) {
        out[label.toLowerCase()] = Math.floor(n);
      }
    }
    return out;
  } catch {
    return {};
  }
}

/** Per-segment chips with peak concurrent counts when available (`person 3`). */
export function parseObjectChips(segment: SegmentObjectSource, max = 8): ObjectChip[] {
  const counts = parseObjectCounts(segment.object_counts);
  const classLabels = dedupeObjectLabels(parseObjectClassList(segment.object_classes));
  const labels = classLabels.length
    ? classLabels
    : dedupeObjectLabels(Object.keys(counts));

  return labels.slice(0, max).map(label => {
    const count = counts[label.toLowerCase()];
    return {
      label,
      count: count && count > 0 ? count : undefined,
      display: count && count > 0 ? `${label} ${count}` : label,
    };
  });
}

/** Union of class names across a chunk timeline (no detection totals). */
export function chunkObjectTags(
  timeline: SegmentObjectSource[],
  max = 8,
): string[] {
  const labels = new Set<string>();
  for (const seg of timeline) {
    parseObjectClassList(seg.object_classes).forEach(label => labels.add(label));
  }
  return dedupeObjectLabels(Array.from(labels)).slice(0, max);
}

export function highlightQueryTerms(text: string, terms: string[]): string {
  if (!text) return '';
  let escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  for (const term of terms) {
    if (!term || term.length < 2) continue;
    escaped = escaped.replace(buildTermPattern(term), '<span class="query-term-text">$1</span>');
  }
  return escaped;
}

export interface PreviewSegmentLike extends SegmentObjectSource {
  segment_number: number;
  segment_start_sec: number;
  segment_end_sec: number;
  source: string;
  reasoning_content?: string | null;
  similarity_score: number;
  query_highlight: boolean;
  is_best_match: boolean;
}

export interface PreviewChunkLike {
  reasoning_content?: string;
  timeline: PreviewSegmentLike[];
}

/** Preview segment for a card. */
export function pickPreviewSegment(timeline: PreviewSegmentLike[]): PreviewSegmentLike | null {
  if (!timeline?.length) return null;
  const queryHits = timeline.filter(seg => seg.query_highlight);
  const pool = queryHits.length
    ? queryHits
    : timeline.filter(seg => seg.is_best_match);
  const candidates = pool.length ? pool : timeline;
  return candidates.reduce((best, seg) => {
    if (!best) return seg;
    if (seg.similarity_score > best.similarity_score) return seg;
    if (seg.similarity_score < best.similarity_score) return best;
    return seg.segment_number < best.segment_number ? seg : best;
  });
}

export function previewCaption(chunk: PreviewChunkLike): string {
  const seg = pickPreviewSegment(chunk.timeline);
  if (seg) {
    return segmentDisplayCaption(seg) || (chunk.reasoning_content || '').trim();
  }
  return (chunk.reasoning_content || '').trim();
}

/** Object chips for the same matched/preview segment as the card caption. */
export function previewObjectTags(chunk: PreviewChunkLike, max = 8): string[] {
  const seg = pickPreviewSegment(chunk.timeline);
  if (!seg) return [];
  return parseStructuredObjects(seg).slice(0, max);
}
