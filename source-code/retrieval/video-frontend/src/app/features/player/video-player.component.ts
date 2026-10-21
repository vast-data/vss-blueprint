import {
  Component,
  Inject,
  OnInit,
  ViewChild,
  ElementRef,
  inject,
  signal,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { DomSanitizer, SafeHtml, SafeResourceUrl } from '@angular/platform-browser';
import { ChunkSearchResult, TimelineSegment, VideoScope, VideoSearchResult } from '../../shared/models/video.model';
import { VideoService } from '../../shared/services/video.service';
import {
  extractHighlightTerms,
  highlightQueryTerms,
  objectMatchesQuery,
  parseObjectChips,
  segmentDisplayCaption,
} from '../../shared/utils/query-highlight.util';
import { playVideoMuted, seekVideoAndWait } from '../../shared/utils/video-hover-preview.util';
import {
  DetectionFrame,
  DetectionSidecar,
  drawDetectionOverlay,
  findFrameForTime,
  readDetectionOverlayPref,
  writeDetectionOverlayPref,
} from '../../shared/utils/detection-overlay.util';
import { VideoSummarizeDialogComponent } from '../explore/components/video-summarize-dialog.component';
import { ExploreService } from '../explore/services/explore.service';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { firstValueFrom } from 'rxjs';

export interface VideoPlayerData {
  chunk?: ChunkSearchResult;
  video?: VideoSearchResult;
  query?: string;
  initialSeekSec?: number;
  mode?: 'search' | 'explore';
  scope?: VideoScope;
}

@Component({
  selector: 'app-video-player',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatButtonToggleModule,
  ],
  template: `
    <div class="video-player-container">
      <div class="player-header">
        <div class="header-info">
          <mat-icon class="video-icon">play_circle</mat-icon>
          <div>
            <h2>{{ title() }}</h2>
            @if (chunk(); as c) {
              @if (isExplore()) {
                <p class="subtitle">
                  @if (!c.stream_id && c.chunk_index != null) {
                    Chunk {{ c.chunk_index + 1 }}@if (c.stream_chunk_total) {/{{ c.stream_chunk_total }}}
                    ·
                  }
                  {{ formatTime(c.chunk_duration_sec) }} ·
                  @if (c.timeline.length < c.total_segments) {
                    {{ c.timeline.length }} of {{ c.total_segments }} segments
                  } @else {
                    {{ c.total_segments }} segments
                  }
                </p>
              } @else {
                <p class="subtitle">
                  Best match {{ formatTime(c.best_match_start_sec) }}–{{ formatTime(c.best_match_end_sec) }}
                  · segment {{ c.best_segment_number }}/{{ c.total_segments }}
                  @if (searchQuery()) {
                    · "{{ searchQuery() }}"
                  }
                </p>
              }
            }
          </div>
        </div>
        <button mat-icon-button class="close-btn" (click)="close()" matTooltip="Close">
          <mat-icon>close</mat-icon>
        </button>
      </div>

      @if (isExplore() && chunk(); as c) {
        <div class="explore-actions-bar">
          @if (c.stream_id) {
            <div class="stream-hop">
              <button
                mat-stroked-button
                type="button"
                class="stream-nav-btn"
                (click)="hopStream('prev')"
                [disabled]="c.prev_chunk_index == null || streamHop()">
                <mat-icon>skip_previous</mat-icon>
                Previous
              </button>
              @if (c.chunk_index != null) {
                <span class="stream-chunk-label">
                  Chunk {{ c.chunk_index + 1 }}@if (c.stream_chunk_total) {/{{ c.stream_chunk_total }}}
                </span>
              }
              <button
                mat-stroked-button
                type="button"
                class="stream-nav-btn"
                (click)="hopStream('next')"
                [disabled]="c.next_chunk_index == null || streamHop()">
                Next
                <mat-icon>skip_next</mat-icon>
              </button>
            </div>
          }
          <button mat-raised-button color="primary" class="summarize-btn" (click)="summarizeVideo()">
            <mat-icon>auto_awesome</mat-icon>
            Summarize Video
          </button>
        </div>
        @if (streamHopError()) {
          <p class="stream-hop-error">{{ streamHopError() }}</p>
        }
      }

      <div class="video-section">
        <div class="video-wrap">
          @if (loading()) {
            <div class="loading-overlay">
              <mat-spinner diameter="40"></mat-spinner>
              <p>Loading video...</p>
            </div>
          }

          @if (streamUrl() && !error()) {
            <video
              #videoPlayer
              [src]="streamUrl()"
              controls
              playsinline
              preload="auto"
              muted
              (loadedmetadata)="onLoadedMetadata()"
              (canplay)="onCanPlay()"
              (playing)="onVideoPlaying()"
              (timeupdate)="onTimeUpdate()"
              (error)="onVideoError($event)"
              class="video-player">
            </video>
            @if (overlayEnabled()) {
              <canvas #overlayCanvas class="detection-overlay"></canvas>
            }
          }
        </div>

        @if (error()) {
          <div class="error-state">
            <mat-icon>error_outline</mat-icon>
            <p>{{ error() }}</p>
            <button mat-raised-button color="primary" (click)="loadVideo()">
              <mat-icon>refresh</mat-icon>
              Retry
            </button>
          </div>
        }
      </div>

      @if (chunk()) {
        <div class="moment-timeline">
          <div class="timeline-header">
            <div class="timeline-title">
              <mat-icon>timeline</mat-icon>
              <span>{{ isExplore() ? 'Segment timeline' : 'Match timeline' }}</span>
            </div>
            <div class="timeline-toolbar">
              <div class="overlay-control">
                <span class="overlay-label">Bboxes</span>
                <mat-button-toggle-group
                  class="overlay-toggle-group"
                  [value]="overlayEnabled() ? 'on' : 'off'"
                  (change)="toggleOverlay($event.value === 'on')"
                  hideSingleSelectionIndicator>
                  <mat-button-toggle value="off">OFF</mat-button-toggle>
                  <mat-button-toggle value="on">ON</mat-button-toggle>
                </mat-button-toggle-group>
              </div>
              <span class="playhead">{{ formatTime(currentTime()) }} / {{ formatTime(chunk()!.chunk_duration_sec) }}</span>
            </div>
          </div>
          <div class="timeline-track">
            @for (seg of chunk()!.timeline; track seg.source) {
              <button
                type="button"
                class="timeline-seg"
                [style.flex]="segmentFlex(seg)"
                [class.active]="activeSegmentNumber() === seg.segment_number"
                [class.search-match]="!isExplore() && seg.is_search_match"
                [class.query-hit]="!isExplore() && seg.query_highlight"
                [class.best-match]="!isExplore() && seg.is_best_match"
                (click)="seekToSegment(seg)"
                [matTooltip]="segmentTooltip(seg)">
                <span class="seg-num">{{ seg.segment_number }}</span>
              </button>
            }
          </div>
        </div>

        <div class="segments-panel">
          @for (seg of chunk()!.timeline; track seg.source) {
            <div
              class="segment-row"
              [class.active]="activeSegmentNumber() === seg.segment_number"
              [class.highlight]="seg.query_highlight">
              <div class="seg-time">
                <button
                  type="button"
                  class="seg-jump"
                  (click)="seekToSegment(seg)"
                  [matTooltip]="'Jump to ' + formatTime(seg.segment_start_sec)">
                  <mat-icon>play_arrow</mat-icon>
                  <span class="seg-range">{{ formatTime(seg.segment_start_sec) }}–{{ formatTime(seg.segment_end_sec) }}</span>
                </button>
                @if (!isExplore()) {
                  @if (seg.is_best_match) {
                    <span class="best-pill">Best match</span>
                  } @else if (seg.query_highlight) {
                    <span class="hit-pill">Query hit</span>
                  }
                }
              </div>
              <p
                class="seg-caption"
                [innerHTML]="segmentCaptionHtml().get(seg.source) ?? ''"></p>
              @if (segmentObjectChips(seg).length) {
                <div class="seg-objects">
                  @for (obj of segmentObjectChips(seg); track obj.label) {
                    <span class="obj-chip" [class.hit]="isQueryTerm(obj.label)">{{ obj.display }}</span>
                  }
                </div>
              }
              @if (!isExplore() && seg.is_search_match && seg.similarity_score > 0) {
                <span class="seg-score">{{ (seg.similarity_score * 100).toFixed(0) }}% relevance</span>
              }
            </div>
          }
        </div>
      } @else if (legacyVideo()) {
        <div class="segment-nav-bar">
          <button mat-icon-button (click)="previousSegment()" [disabled]="isFirstSegment()">
            <mat-icon>skip_previous</mat-icon>
          </button>
          <span>Segment {{ legacyVideo()!.segment_number }}/{{ legacyVideo()!.total_segments }}</span>
          <button mat-icon-button (click)="nextSegment()" [disabled]="isLastSegment()">
            <mat-icon>skip_next</mat-icon>
          </button>
        </div>
        <div class="info-section">
          <div class="reasoning-card">
            <p>{{ legacyVideo()!.reasoning_content }}</p>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .video-player-container {
      display: flex;
      flex-direction: column;
      height: 100%;
      max-height: 92vh;
      background: var(--bg-primary);
      color: var(--text-primary);
      overflow: hidden;
    }

    .player-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem 1.25rem;
      background: var(--bg-card);
      border-bottom: 1px solid var(--border-color);

      h2 {
        margin: 0;
        font-size: 1.45rem;
        font-weight: 650;
        max-width: 720px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .subtitle {
        margin: 0.3rem 0 0;
        font-size: 1.12rem;
        font-weight: 600;
        color: var(--text-primary);
      }

      .video-icon { color: var(--accent-primary); }
    }

    .explore-actions-bar {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.75rem;
      padding: 0.85rem 1rem;
      background: rgba(115, 200, 253, 0.08);
      border-bottom: 1px solid rgba(115, 200, 253, 0.22);
    }

    .stream-hop {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 1.25rem;
      width: 100%;
      margin: 0;
    }

    .stream-chunk-label {
      min-width: 9rem;
      text-align: center;
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: 0.01em;
      color: #f4fbff;
    }

    :host ::ng-deep .stream-nav-btn {
      min-width: 148px;
      color: #f4fbff !important;
      border-color: #8fd4ff !important;
      background: rgba(115, 200, 253, 0.28) !important;
      font-weight: 650;
    }

    :host ::ng-deep .stream-nav-btn .mdc-button__label,
    :host ::ng-deep .stream-nav-btn mat-icon {
      color: #f4fbff !important;
    }

    :host ::ng-deep .stream-nav-btn:disabled,
    :host ::ng-deep .stream-nav-btn:disabled .mdc-button__label,
    :host ::ng-deep .stream-nav-btn:disabled mat-icon {
      color: rgba(244, 251, 255, 0.45) !important;
      border-color: rgba(143, 212, 255, 0.35) !important;
    }

    .stream-hop-error {
      margin: 0;
      padding: 0.35rem 1rem 0.55rem;
      color: #f87171;
      font-size: 0.85rem;
    }

    .summarize-btn {
      align-self: flex-end;
      background: var(--button-bg-primary) !important;
      color: var(--button-text) !important;

      mat-icon {
        color: var(--button-text) !important;
      }
    }

    .video-section {
      position: relative;
      background: #000;
      flex-shrink: 0;
      max-height: 42vh;

      .video-wrap {
        position: relative;
        width: 100%;
        min-height: 200px;
        max-height: 42vh;
        overflow: hidden;
      }

      .video-player {
        width: 100%;
        max-height: 42vh;
        object-fit: contain;
        display: block;
        vertical-align: top;
      }

      .detection-overlay {
        position: absolute;
        left: 0;
        top: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
      }

      .loading-overlay {
        position: absolute;
        inset: 0;
        z-index: 3;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        background: rgba(0, 0, 0, 0.72);
        pointer-events: none;

        p {
          margin: 0;
          font-size: 0.82rem;
          color: rgba(255, 255, 255, 0.85);
        }
      }
    }

    .overlay-control {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      flex-shrink: 0;
    }

    .overlay-label {
      font-size: 0.79rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #22c55e !important;
      white-space: nowrap;
    }

    .overlay-toggle-group {
      ::ng-deep .mat-button-toggle,
      ::ng-deep .mat-mdc-button-toggle {
        min-width: 2.6rem;
      }

      ::ng-deep .mat-button-toggle .mat-button-toggle-label-content,
      ::ng-deep .mat-mdc-button-toggle .mat-button-toggle-label-content {
        color: var(--text-primary) !important;
        font-size: 0.79rem;
        font-weight: 600;
        line-height: 28px;
        padding: 0 0.6rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      ::ng-deep .mat-button-toggle-button,
      ::ng-deep .mat-mdc-button-toggle-button {
        background: var(--bg-secondary);
        height: 28px;
      }

      ::ng-deep .mat-button-toggle-checked,
      ::ng-deep .mat-mdc-button-toggle-checked {
        background: var(--bg-card-hover) !important;
      }

      ::ng-deep mat-button-toggle[value='on'].mat-button-toggle-checked .mat-button-toggle-label-content,
      ::ng-deep .mat-mdc-button-toggle.mat-button-toggle-checked[value='on'] .mat-button-toggle-label-content {
        color: #22c55e !important;
      }

      ::ng-deep mat-button-toggle[value='off'].mat-button-toggle-checked .mat-button-toggle-label-content,
      ::ng-deep .mat-mdc-button-toggle.mat-button-toggle-checked[value='off'] .mat-button-toggle-label-content {
        color: var(--text-primary) !important;
      }
    }

    .error-state {
      min-height: 220px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 0.75rem;
      padding: 1.5rem;
    }

    .moment-timeline {
      padding: 0.75rem 1rem 0.5rem;
      border-bottom: 1px solid var(--border-color);
      background: var(--bg-card);
    }

    .timeline-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.65rem;
      flex-wrap: wrap;
      margin-bottom: 0.45rem;
    }

    .timeline-title {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-muted);
    }

    .timeline-toolbar {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-left: auto;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .timeline-header .playhead {
      font-family: monospace;
      font-size: 0.78rem;
      color: var(--text-secondary);
      text-transform: none;
      letter-spacing: 0;
      white-space: nowrap;
    }

    .timeline-track {
      display: flex;
      gap: 3px;
      height: 36px;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border-color);
    }

    .timeline-seg {
      position: relative;
      border: none;
      padding: 0;
      min-width: 10px;
      background: rgba(148, 163, 184, 0.22);
      cursor: pointer;
      transition: transform 0.15s, box-shadow 0.15s;

      &:hover { transform: scaleY(1.06); }

      &.search-match { background: rgba(59, 130, 246, 0.4); }
      &.query-hit { background: rgba(34, 197, 94, 0.5); }
      &.best-match {
        background: linear-gradient(180deg, #22c55e, #16a34a);
      }
      &.active {
        box-shadow: inset 0 0 0 2px #fff, 0 0 0 2px #22c55e;
        z-index: 1;
      }

      .seg-num {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.68rem;
        font-weight: 700;
        color: rgba(255, 255, 255, 0.9);
      }
    }

    .segments-panel {
      flex: 1;
      overflow-y: auto;
      padding: 0.75rem 1rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.55rem;
      user-select: text;
      -webkit-user-select: text;
    }

    .segment-row {
      text-align: left;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 0.65rem 0.75rem;
      background: var(--bg-card);
      cursor: default;
      transition: border-color 0.2s, background 0.2s;

      &.active {
        border-color: rgba(34, 197, 94, 0.65);
        box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.25);
      }
      &.highlight {
        border-color: rgba(34, 197, 94, 0.45);
        background: rgba(34, 197, 94, 0.06);
      }
    }

    .seg-time {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.35rem;
    }

    .seg-jump {
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
      margin: 0;
      padding: 0.15rem 0.45rem 0.15rem 0.15rem;
      border: 1px solid transparent;
      border-radius: 8px;
      background: transparent;
      color: inherit;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;

      mat-icon {
        font-size: 1.1rem;
        width: 1.1rem;
        height: 1.1rem;
        color: var(--accent-primary);
      }

      &:hover {
        background: rgba(34, 197, 94, 0.12);
        border-color: rgba(34, 197, 94, 0.35);
      }
    }

    .seg-range {
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--accent-primary);
      font-family: monospace;
    }

    .best-pill, .hit-pill {
      font-size: 0.65rem;
      padding: 0.1rem 0.4rem;
      border-radius: 999px;
      font-weight: 700;
      text-transform: uppercase;
    }

    .best-pill {
      background: rgba(34, 197, 94, 0.2);
      color: #22c55e;
    }

    .hit-pill {
      background: rgba(34, 197, 94, 0.12);
      color: #86efac;
    }

    .seg-caption {
      margin: 0 0 0.4rem;
      font-size: 0.85rem;
      line-height: 1.5;
      color: var(--text-secondary);
      cursor: text !important;
      user-select: text !important;
      -webkit-user-select: text !important;

      ::ng-deep .query-term-text {
        color: #22c55e;
        font-weight: 600;
        cursor: text !important;
        user-select: text !important;
      }
    }

    .seg-objects {
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem;
      margin-bottom: 0.25rem;
    }

    .obj-chip {
      font-size: 0.68rem;
      padding: 0.1rem 0.4rem;
      border-radius: 999px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      color: var(--text-muted);

      &.hit {
        color: #22c55e;
        font-weight: 600;
      }
    }

    .seg-score {
      font-size: 0.72rem;
      color: #73c8fd;
    }

    .segment-nav-bar, .info-section, .reasoning-card {
      padding: 0.75rem 1rem;
    }
  `],
})
export class VideoPlayerComponent implements OnInit {
  private videoService = inject(VideoService);
  private explore = inject(ExploreService);
  private sanitizer = inject(DomSanitizer);
  private dialogRef = inject(MatDialogRef<VideoPlayerComponent>);
  private dialog = inject(MatDialog);

  @ViewChild('videoPlayer') videoPlayer?: ElementRef<HTMLVideoElement>;
  @ViewChild('overlayCanvas') overlayCanvas?: ElementRef<HTMLCanvasElement>;

  loading = signal(true);
  error = signal<string | null>(null);
  streamUrl = signal<SafeResourceUrl | null>(null);
  currentTime = signal(0);
  overlayEnabled = signal(readDetectionOverlayPref());
  detectionFrames = signal<DetectionFrame[]>([]);
  chunk = signal<ChunkSearchResult | null>(null);
  legacyVideo = signal<VideoSearchResult | null>(null);
  queryTerms = signal<string[]>([]);
  mode = signal<'search' | 'explore'>('search');
  pendingSeekSec: number | null = null;
  private metadataReady = false;
  private autoPlayPending = true;
  private playbackPrepared = false;
  /** Chunk timeline second where the loaded segment MP4 starts. */
  private segmentPlaybackOffset = 0;
  private pendingInlineSeekSec = 0;
  private loadedSegmentSource = '';
  private loadedDetectionSource = '';
  private detectionLoadToken = 0;

  streamHop = signal(false);
  streamHopError = signal<string | null>(null);

  title = computed(() => this.chunk()?.filename ?? this.legacyVideo()?.filename ?? 'Video');

  searchQuery = computed(() => (this.data.query ?? this.chunk()?.query ?? '').trim());

  /** Cached caption HTML per segment. */
  segmentCaptionHtml = computed(() => {
    const c = this.chunk();
    const terms = this.queryTerms();
    const out = new Map<string, SafeHtml>();
    if (!c) return out;
    for (const seg of c.timeline) {
      const text = segmentDisplayCaption(seg);
      const html = text ? highlightQueryTerms(text, terms) : '';
      out.set(seg.source, this.sanitizer.bypassSecurityTrustHtml(html));
    }
    return out;
  });

  activeSegmentNumber = computed(() => {
    const c = this.chunk();
    const t = this.currentTime();
    if (!c) return 0;
    for (const seg of c.timeline) {
      if (t >= seg.segment_start_sec && t < seg.segment_end_sec) {
        return seg.segment_number;
      }
    }
    const last = c.timeline[c.timeline.length - 1];
    if (last && t >= last.segment_start_sec) return last.segment_number;
    return c.best_segment_number;
  });

  constructor(@Inject(MAT_DIALOG_DATA) public data: VideoPlayerData) {}

  ngOnInit() {
    this.mode.set(this.data.mode ?? 'search');
    if (this.data.chunk) {
      this.chunk.set(this.data.chunk);
      this.queryTerms.set(extractHighlightTerms(this.data.query ?? this.data.chunk.query));
      const targetSeek =
        this.data.initialSeekSec ??
        this.data.chunk.best_match_start_sec ??
        0;
      this.beginSegmentPlayback(this.data.chunk, targetSeek);
    } else if (this.data.video) {
      this.legacyVideo.set(this.data.video);
      this.loadLegacyVideo();
    }
  }

  /** Search: segment MP4s. Explore: full parent chunk from original_video. */
  private beginSegmentPlayback(chunk: ChunkSearchResult, seekSec: number) {
    if (this.isExplore()) {
      const source = chunk.original_video?.trim() ?? '';
      this.segmentPlaybackOffset = 0;
      this.pendingInlineSeekSec = Math.max(0, seekSec);
      this.loadedSegmentSource = source;
      this.loadStreamSource(source);
      this.syncDetectionForChunkTime(chunk, seekSec);
      return;
    }

    const seg = this.findSegmentForTime(chunk, seekSec);
    if (seg?.source?.trim()) {
      const startWithin = Math.max(0, seekSec - seg.segment_start_sec);
      this.segmentPlaybackOffset = seg.segment_start_sec;
      this.pendingInlineSeekSec = startWithin;
      this.loadedSegmentSource = seg.source.trim();
      this.loadStreamSource(this.loadedSegmentSource);
      this.loadedDetectionSource = '';
      this.syncDetectionForChunkTime(chunk, seekSec);
      return;
    }
    this.segmentPlaybackOffset = 0;
    this.pendingInlineSeekSec = 0;
    this.pendingSeekSec = seekSec;
    this.loadedSegmentSource = chunk.original_video?.trim() ?? '';
    this.loadStreamSource(this.loadedSegmentSource);
  }

  private findSegmentForTime(
    chunk: ChunkSearchResult,
    sec: number,
  ): TimelineSegment | undefined {
    for (const seg of chunk.timeline) {
      if (sec >= seg.segment_start_sec && sec < seg.segment_end_sec) {
        return seg;
      }
    }
    const best = chunk.timeline.find((s) => s.is_best_match);
    if (best) return best;
    return chunk.timeline[0];
  }

  private loadLegacyVideo() {
    const source = this.legacyVideo()?.source?.trim() ?? '';
    this.segmentPlaybackOffset = 0;
    this.pendingInlineSeekSec = 0;
    this.pendingSeekSec = null;
    this.loadedSegmentSource = source;
    this.loadStreamSource(source);
  }

  private loadStreamSource(source: string) {
    this.loading.set(true);
    this.error.set(null);
    this.metadataReady = false;
    this.autoPlayPending = true;
    this.playbackPrepared = false;

    try {
      const token = localStorage.getItem('video_lab_token');
      if (!token) throw new Error('No authentication token found');
      if (!source) throw new Error('No video source available');

      const streamUrl = this.videoService.getStreamUrl(source, token);
      this.streamUrl.set(this.sanitizer.bypassSecurityTrustResourceUrl(streamUrl));
    } catch (err: any) {
      this.error.set(err.message ?? 'Failed to load video');
      this.loading.set(false);
    }
  }

  loadVideo() {
    const c = this.chunk();
    if (c && this.loadedSegmentSource) {
      this.loadStreamSource(this.loadedSegmentSource);
      return;
    }
    this.loadLegacyVideo();
  }

  onLoadedMetadata() {
    this.metadataReady = true;
    this.refreshDetectionOverlay();
    void this.prepareAndPlay();
  }

  onCanPlay() {
    void this.prepareAndPlay();
  }

  onVideoPlaying() {
    this.loading.set(false);
  }

  private clearLoadingOverlay() {
    this.loading.set(false);
  }

  private async prepareAndPlay() {
    if (!this.metadataReady || this.playbackPrepared || !this.autoPlayPending) return;

    const el = this.videoPlayer?.nativeElement;
    if (!el) return;

    const chunkSeek = this.pendingSeekSec;
    this.pendingSeekSec = null;
    const inlineSeek = this.pendingInlineSeekSec;
    this.pendingInlineSeekSec = 0;

    try {
      if (chunkSeek != null && chunkSeek > 0.05) {
        this.loading.set(true);
        await seekVideoAndWait(el, chunkSeek);
      } else if (inlineSeek > 0.05) {
        this.loading.set(true);
        await seekVideoAndWait(el, inlineSeek);
      }

      this.playbackPrepared = true;
      this.autoPlayPending = false;
      await playVideoMuted(el);
    } catch {
      // autoplay may be blocked
    } finally {
      this.clearLoadingOverlay();
    }
  }

  onTimeUpdate() {
    const el = this.videoPlayer?.nativeElement;
    if (!el) return;

    const c = this.chunk();
    if (c && this.isExplore()) {
      this.currentTime.set(el.currentTime);
      this.syncDetectionForChunkTime(c, el.currentTime);
    } else {
      this.currentTime.set(this.segmentPlaybackOffset + el.currentTime);
    }
    this.refreshDetectionOverlay();
  }

  toggleOverlay(enabled: boolean) {
    this.overlayEnabled.set(enabled);
    writeDetectionOverlayPref(enabled);
    if (enabled) {
      const c = this.chunk();
      if (c) {
        this.syncDetectionForChunkTime(c, this.currentTime());
      } else if (this.loadedSegmentSource) {
        void this.loadDetectionsForSource(this.loadedSegmentSource);
      }
    } else {
      this.detectionFrames.set([]);
      this.loadedDetectionSource = '';
      this.refreshDetectionOverlay();
    }
  }

  private syncDetectionForChunkTime(chunk: ChunkSearchResult, sec: number) {
    if (!this.overlayEnabled()) return;
    const seg = this.findSegmentForTime(chunk, sec);
    const src = seg?.source?.trim() ?? '';
    if (!src || src === this.loadedDetectionSource) return;
    this.loadedDetectionSource = src;
    void this.loadDetectionsForSource(src);
  }

  private async loadDetectionsForSource(source: string) {
    if (!this.overlayEnabled() || !source) {
      this.detectionFrames.set([]);
      return;
    }
    const token = ++this.detectionLoadToken;
    try {
      const payload = await firstValueFrom(this.videoService.getDetections(source));
      if (token !== this.detectionLoadToken) return;
      const sidecar = payload as DetectionSidecar;
      this.detectionFrames.set(sidecar.frames ?? []);
      this.refreshDetectionOverlay();
    } catch {
      if (token !== this.detectionLoadToken) return;
      this.detectionFrames.set([]);
      this.refreshDetectionOverlay();
    }
  }

  private detectionOverlayTimeSec(): number {
    const el = this.videoPlayer?.nativeElement;
    if (!el) return 0;

    const c = this.chunk();
    if (c && this.isExplore()) {
      const seg = this.findSegmentForTime(c, el.currentTime);
      if (seg) {
        return Math.max(0, el.currentTime - seg.segment_start_sec);
      }
    }
    return el.currentTime;
  }

  private refreshDetectionOverlay() {
    if (!this.overlayEnabled()) return;
    const canvas = this.overlayCanvas?.nativeElement;
    const video = this.videoPlayer?.nativeElement;
    if (!canvas || !video) return;
    const frame = findFrameForTime(this.detectionFrames(), this.detectionOverlayTimeSec());
    drawDetectionOverlay(canvas, video, frame);
  }

  onVideoError(event?: Event) {
    console.error('[VIDEO PLAYER]', event);
    this.error.set('Failed to load video. Please try again.');
    this.loading.set(false);
  }

  seekToSegment(seg: TimelineSegment) {
    const start = Math.max(0, seg.segment_start_sec);
    this.seekTo(start > 0 ? start + 0.05 : 0);
  }

  seekTo(sec: number) {
    const c = this.chunk();
    if (c && this.isExplore()) {
      const el = this.videoPlayer?.nativeElement;
      if (!el) return;
      void this.seekAndPlay(el, sec, () => {
        this.currentTime.set(sec);
        this.syncDetectionForChunkTime(c, sec);
        this.refreshDetectionOverlay();
      });
      return;
    }

    if (c) {
      const seg = this.findSegmentForTime(c, sec);
      if (seg?.source?.trim()) {
        const source = seg.source.trim();
        const startWithin = Math.max(0, sec - seg.segment_start_sec);
        if (source !== this.loadedSegmentSource) {
          this.segmentPlaybackOffset = seg.segment_start_sec;
          this.pendingInlineSeekSec = startWithin;
          this.loadedSegmentSource = source;
          this.loadedDetectionSource = '';
          this.loadStreamSource(source);
          this.syncDetectionForChunkTime(c, sec);
          return;
        }
        const el = this.videoPlayer?.nativeElement;
        if (!el) return;
        void this.seekAndPlay(el, startWithin);
        return;
      }
    }

    const el = this.videoPlayer?.nativeElement;
    if (!el) return;
    void this.seekAndPlay(el, sec);
  }

  private async seekAndPlay(
    el: HTMLVideoElement,
    sec: number,
    afterSeek?: () => void,
  ): Promise<void> {
    this.loading.set(true);
    try {
      await seekVideoAndWait(el, sec);
      afterSeek?.();
      await playVideoMuted(el);
    } catch {
      // seek or autoplay failed
    } finally {
      this.clearLoadingOverlay();
    }
  }

  segmentFlex(seg: TimelineSegment): string {
    const len = Math.max(seg.segment_end_sec - seg.segment_start_sec, 0.5);
    return `${len} 1 0`;
  }

  formatTime(seconds: number): string {
    if (seconds == null || Number.isNaN(seconds)) return '0:00';
    const total = Math.floor(seconds);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  segmentTooltip(seg: TimelineSegment): string {
    return `#${seg.segment_number} ${this.formatTime(seg.segment_start_sec)}–${this.formatTime(seg.segment_end_sec)}`;
  }

  segmentObjectChips(seg: TimelineSegment) {
    return parseObjectChips(seg, 8);
  }

  isQueryTerm(label: string): boolean {
    return objectMatchesQuery(label, this.queryTerms());
  }

  isFirstSegment(): boolean {
    return (this.legacyVideo()?.segment_number ?? 1) === 1;
  }

  isLastSegment(): boolean {
    const v = this.legacyVideo();
    return !!v && v.segment_number === v.total_segments;
  }

  async previousSegment() {
    const v = this.legacyVideo();
    if (!v || this.isFirstSegment()) return;
    const n = v.segment_number - 1;
    const newSource = v.source.replace(
      `_segment_${String(v.segment_number).padStart(3, '0')}_of_`,
      `_segment_${String(n).padStart(3, '0')}_of_`
    );
    try {
      const metadata = await this.videoService.getVideoMetadata(newSource).toPromise();
      this.legacyVideo.set({ ...v, ...metadata, source: newSource, segment_number: n });
    } catch {
      this.legacyVideo.set({ ...v, source: newSource, segment_number: n });
    }
    this.loadLegacyVideo();
  }

  async nextSegment() {
    const v = this.legacyVideo();
    if (!v || this.isLastSegment()) return;
    const n = v.segment_number + 1;
    const newSource = v.source.replace(
      `_segment_${String(v.segment_number).padStart(3, '0')}_of_`,
      `_segment_${String(n).padStart(3, '0')}_of_`
    );
    try {
      const metadata = await this.videoService.getVideoMetadata(newSource).toPromise();
      this.legacyVideo.set({ ...v, ...metadata, source: newSource, segment_number: n });
    } catch {
      this.legacyVideo.set({ ...v, source: newSource, segment_number: n });
    }
    this.loadLegacyVideo();
  }

  isExplore(): boolean {
    return this.mode() === 'explore';
  }

  async hopStream(direction: 'prev' | 'next') {
    const current = this.chunk();
    if (!current?.stream_id || this.streamHop()) return;
    const index = direction === 'prev' ? current.prev_chunk_index : current.next_chunk_index;
    if (index == null) return;
    this.streamHop.set(true);
    this.streamHopError.set(null);
    try {
      const next = await firstValueFrom(
        this.explore.getStreamChunk(current.stream_id, index, this.data.scope ?? 'all'),
      );
      this.chunk.set(next);
      this.beginSegmentPlayback(next, 0);
    } catch {
      this.streamHopError.set('Could not open that stream chunk.');
    } finally {
      this.streamHop.set(false);
    }
  }

  summarizeVideo() {
    const c = this.chunk();
    if (!c) return;
    this.dialog.open(VideoSummarizeDialogComponent, {
      width: '720px',
      maxWidth: '95vw',
      maxHeight: '90vh',
      panelClass: 'video-summarize-dialog',
      data: { chunk: c },
    });
  }

  close() {
    this.dialogRef.close();
  }
}
