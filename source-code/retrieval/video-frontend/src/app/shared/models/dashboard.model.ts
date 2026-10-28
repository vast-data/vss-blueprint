export interface CountItem {
  label: string;
  count: number;
}

export interface ObjectStatItem {
  label: string;
  segment_count: number;
  instance_count: number;
}

export interface UploadDayItem {
  date: string;
  segment_rows: number;
}

export interface S3Inventory {
  chunks_bucket: string;
  chunks_mp4?: number | null;
  segments_bucket: string;
  segments_mp4?: number | null;
  segmenter_output_bucket: string;
  segmenter_output_mp4?: number | null;
  errors?: Record<string, string> | null;
}

export interface PipelineAlignment {
  segments_s3_mp4?: number | null;
  indexed_clips?: number;
  segment_rows?: number;
  pending_index?: number | null;
  re_ingest_excess?: number;
  indexed_matches_segments_s3?: boolean | null;
  rows_match_segments_s3?: boolean | null;
  segments_bucket_matches_segmenter?: boolean | null;
  healthy?: boolean | null;
}

export interface DashboardOverview {
  total_rows: number;
  segment_rows: number;
  other_rows: number;
  unique_videos: number;
  fully_indexed_videos?: number;
  indexed_clips?: number;
  re_ingest_rows?: number;
  re_ingest_clips?: number;
  stream_sessions?: number;
  duplicate_segment_slots: number;
  duplicate_segment_rows: number;
  public_segment_rows: number;
  private_segment_rows: number;
}

export interface DashboardQuality {
  reasoning_ok: number;
  reasoning_ok_pct: number;
  perception_ok: number;
  perception_ok_pct: number;
  with_object_classes: number;
  with_object_classes_pct: number;
}

export interface DashboardModels {
  reasoner_calls: number;
  token_total: number;
  token_avg: number;
  cached_tokens: number;
}

export type LiveEndpointStatus = 'ready' | 'busy' | 'down' | 'unavailable';

export interface LiveEndpoint {
  name: string;
  label: string;
  status: LiveEndpointStatus | string;
  model: string;
  detail?: string | null;
  running?: number | null;
  waiting?: number | null;
  cache_pct?: number | null;
  avg_latency_sec?: number | null;
  gpu_memory_pct?: number | null;
  gpu_util_pct?: number | null;
  ttft_sec?: number | null;
  prefill_sec?: number | null;
  decode_sec?: number | null;
  finished?: number | null;
  cut_off?: number | null;
  failed?: number | null;
  metrics_available: boolean;
}

export interface LiveEndpoints {
  probed_at: string;
  reasoner: LiveEndpoint;
  embed: LiveEndpoint;
}

export interface RecentVideoItem {
  original_video: string;
  filename: string;
  stream_id?: string | null;
  segment_rows: number;
  indexed_clips?: number;
  chunk_count?: number;
  re_ingest_rows?: number;
  stream_span_sec?: number | null;
  ingest_kind?: string | null;
  unique_segments: number;
  expected_segments: number;
  duplicate_rows: number;
  upload_timestamp?: string | null;
  camera_id: string;
  capture_type: string;
  location: string;
  is_public: boolean;
}

export interface DashboardStatsResponse {
  table: string;
  table_available: boolean;
  table_message?: string | null;
  generated_at: string;
  query_time_ms: number;
  scope: string;
  overview: DashboardOverview;
  quality: DashboardQuality;
  models?: DashboardModels | null;
  live_endpoints?: LiveEndpoints | null;
  objects: ObjectStatItem[];
  metadata: Record<string, CountItem[]>;
  uploads_by_day: UploadDayItem[];
  recent_videos: RecentVideoItem[];
  s3_inventory?: S3Inventory | null;
  pipeline_alignment?: PipelineAlignment | null;
}

export type DashboardScope = 'all' | 'mine' | 'public';
