export const BASE_PATH = "/app";

export type TimeFilter = "1h" | "24h" | "7d" | "all";

export type Camera = {
  id: string;
  label: string;
  camera_id: string;
  source: string;
  start_sec: number;
  reasoning: string;
  ok: boolean;
};

export type AlertItem = {
  id: string;
  kind: string;
  label: string;
  card_id: string;
  summary: string;
  source: string;
  start_sec: number;
};

export type FeedSummary = {
  kind: string;
  label: string;
  card_id: string;
  text: string;
};

export function apiUrl(path: string): string {
  return `${BASE_PATH}${path}`;
}

export function clipUrl(source: string): string {
  return apiUrl(`/api/clip?source=${encodeURIComponent(source)}`);
}

export function withWindow(path: string, timeFilter: TimeFilter, date: string): string {
  const q = new URLSearchParams();
  if (date) {
    q.set("date", date);
  } else {
    q.set("time_filter", timeFilter);
  }
  const qs = q.toString();
  return apiUrl(`${path}${qs ? `?${qs}` : ""}`);
}
