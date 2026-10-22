export interface TimelineSegment {
  segment_number: number;
  segment_start_sec: number;
  segment_end_sec: number;
  source: string;
  reasoning_content: string;
  object_classes?: string;
  object_counts?: string | null;
  perception_ok?: boolean;
  similarity_score: number;
  is_search_match: boolean;
  query_highlight: boolean;
  is_best_match: boolean;
}

export interface ChunkSearchResult {
  original_video: string;
  filename: string;
  chunk_duration_sec: number;
  total_segments: number;
  similarity_score: number;
  best_segment_number: number;
  best_match_start_sec: number;
  best_match_end_sec: number;
  preview_source: string;
  reasoning_content: string;
  is_public: boolean;
  upload_timestamp: string;
  tags: string[];
  matched_segment_count: number;
  query: string;
  timeline: TimelineSegment[];
  camera_id?: string;
  capture_type?: string;
  location?: string;
  cosmos_model?: string;
  tokens_used?: number;
  cached_prompt_tokens?: number | null;
  stream_id?: string | null;
  chunk_index?: number | null;
  stream_chunk_total?: number | null;
  prev_chunk_index?: number | null;
  next_chunk_index?: number | null;
}

export interface VideoSearchResult {
  filename: string;
  source: string;
  reasoning_content: string;
  is_public: boolean;
  upload_timestamp: string;
  duration: number;
  segment_number: number;
  total_segments: number;
  segment_start_sec: number;
  segment_end_sec: number;
  original_video: string;
  tags: string[];
  similarity_score: number;
  cosmos_model?: string;
  tokens_used?: number;
  /** Prompt tokens from KV/prefix cache when Cosmos/vLLM reports usage.prompt_tokens_details.cached_tokens */
  cached_prompt_tokens?: number | null;
  // Stream capture metadata
  camera_id?: string;
  capture_type?: string;
  location?: string;
  scenario?: string;  // Analysis scenario (surveillance, traffic, egocentric, etc.)
}

export interface SearchRequest {
  query: string;
  top_k?: number;
  tags?: string[];
  include_public?: boolean;
  public_only?: boolean;  // When true, only public videos (scope "Public Only")
  system_prompt?: string;  // Custom LLM system prompt (overrides backend default)
  time_filter?: string;  // 'all', '5m', '15m', '1h', '24h', '7d', 'custom'
  custom_start_date?: string;  // ISO 8601 format for custom date range
  custom_end_date?: string;    // ISO 8601 format for custom date range
  metadata_filters?: Record<string, any>;  // Dynamic metadata filters
  min_similarity?: number;  // Minimum similarity score threshold (0.1 - 0.8)
  llm_top_n?: number;  // Number of results to send to LLM for analysis
  hybrid_text_weight?: number;
}

export interface LLMSynthesis {
  response: string;
  segments_used: number;
  segments_analyzed: string[];
  model: string;
  tokens_used: number;
  processing_time: number;
  error?: string | null;
}

export interface SearchResponse {
  results: VideoSearchResult[];
  chunk_results?: ChunkSearchResult[];
  total: number;
  chunk_total?: number;
  query: string;
  embedding_time_ms: number;
  search_time_ms: number;
  permission_filtered: number;
  llm_synthesis?: LLMSynthesis | null;
  sql_query?: string;
}

export interface UploadRequest {
  is_public: boolean;
  tags: string[];
  allowed_users: string[];
}

export interface UploadResponse {
  upload_url: string;
  object_key: string;
  expires_in: number;
  fields: Record<string, string>;  // Presigned POST fields (for S3 upload)
  metadata: Record<string, any>;   // Metadata for display only
}

export interface MetadataField {
  name: string;
  type: string;
  ui_type: 'text' | 'select' | 'number' | 'checkbox' | 'datetime' | 'tags';
  label: string;
  options?: string[];
}

export interface MetadataSchema {
  schema: MetadataField[];
  table: string;
}

export type VideoScope = 'all' | 'mine' | 'public';

export interface ExploreUploadDay {
  date: string;
  chunk_count: number;
}

export interface ExploreLocationItem {
  label: string;
  chunk_count: number;
}

export interface ExploreResponse {
  chunks: ChunkSearchResult[];
  total: number;
  uploads_by_day: ExploreUploadDay[];
  locations?: ExploreLocationItem[];
  cameras?: ExploreLocationItem[];
  scope: VideoScope;
  selected_date?: string | null;
  selected_location?: string | null;
  selected_camera_id?: string | null;
  indexed?: 'complete' | 'partial';
  limit: number;
  offset: number;
  table_available?: boolean;
  table_message?: string | null;
}

export interface VideoDeleteResponse {
  original_video: string;
  segments_deleted: number;
  prompts_deleted: number;
  objects_deleted: number;
  object_errors: string[];
}

export interface VideoSynthesizeRequest {
  original_video: string;
  question?: string;
  max_segments?: number;
  system_prompt?: string;
}

export interface VideoSynthesizeResponse {
  original_video: string;
  segment_count: number;
  segments_used: number;
  answer: string;
  llm_synthesis: LLMSynthesis;
  generated_at: string;
}

