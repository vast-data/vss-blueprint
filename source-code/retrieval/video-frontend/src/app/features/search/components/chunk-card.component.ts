import {
  Component,
  EventEmitter,
  Input,
  Output,
  ViewChild,
  ElementRef,
  inject,
  OnChanges,
  SimpleChanges,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { ChunkSearchResult } from '../../../shared/models/video.model';
import { VideoService } from '../../../shared/services/video.service';
import {
  extractHighlightTerms,
  highlightQueryTerms,
  objectMatchesQuery,
  previewCaption,
  previewObjectTags,
} from '../../../shared/utils/query-highlight.util';
import { explorePlayLabel as formatExplorePlayLabel } from '../../../shared/utils/chunk-display.util';
import { formatAbsoluteTime, formatUploadBadgeTime } from '../../../shared/utils/time.util';
import { BackendModelsService } from '../../../shared/services/backend-models.service';
import { playHoverPreview, stopHoverPreview, claimHoverPreview, releaseHoverPreview } from '../../../shared/utils/video-hover-preview.util';

@Component({
  selector: 'app-chunk-card',
  standalone: true,
  imports: [
    CommonModule,
    MatCardModule,
    MatIconModule,
    MatChipsModule,
    MatTooltipModule,
    MatButtonModule,
    MatProgressSpinnerModule,
  ],
  template: `
        <mat-card class="chunk-card"
              (mouseenter)="onHoverStart()"
              (mouseleave)="onHoverEnd()">
      <div class="video-preview-container" (click)="onOpen()">
        <video
          #videoElement
          [src]="previewUrl || null"
          class="video-preview"
          muted
          [loop]="true"
          playsinline
          preload="metadata"
          (loadedmetadata)="onPreviewFrameReady()"
          (loadeddata)="onVideoLoaded()">
        </video>
        @if (previewLoading) {
          <div class="preview-loading">
            <mat-spinner diameter="32"></mat-spinner>
          </div>
        }
        <div class="play-overlay" [class.hidden]="isPlaying || previewFrameReady">
          <mat-icon>play_circle_filled</mat-icon>
        </div>
        <div class="upload-badge" [matTooltip]="uploadTooltip()">
          <mat-icon>schedule</mat-icon>
          {{ formatUploadDate(chunk.upload_timestamp) }}
        </div>
        @if (mode === 'search') {
        <div class="score-badge">
          {{ (chunk.similarity_score * 100).toFixed(0) }}% match
        </div>
        }
      </div>

      <mat-card-content>
        <div class="title-row">
          <h3 class="chunk-title" [class.explore-title]="mode === 'explore'">{{ chunk.filename }}</h3>
          @if (mode !== 'explore') {
            <span class="duration-chip">{{ formatTime(chunk.chunk_duration_sec) }}</span>
          }
        </div>

        @if (mode === 'search') {
          <p class="match-line">
            Best moment
            <strong>{{ formatTime(chunk.best_match_start_sec) }}–{{ formatTime(chunk.best_match_end_sec) }}</strong>
            · segment {{ chunk.best_segment_number }}/{{ chunk.total_segments }}
            @if (chunk.matched_segment_count > 1) {
              <span class="multi-hit">· {{ chunk.matched_segment_count }} hits</span>
            }
          </p>
        } @else {
          <div class="explore-badges">
            @if (exploreChunkLabel(); as chunkLabel) {
              <span class="meta-badge chunk" matTooltip="Position in this capture">
                <span class="badge-k">Chunk</span>
                <span class="badge-v">{{ chunkLabel }}</span>
              </span>
            }
            <span class="meta-badge time" matTooltip="Chunk length">
              <span class="badge-k">Time</span>
              <span class="badge-v">{{ formatTime(chunk.chunk_duration_sec) }}</span>
            </span>
            <span class="meta-badge segs" [matTooltip]="exploreSegmentTip()">
              <span class="badge-k">Segments</span>
              <span class="badge-v">{{ exploreSegmentLabel() }}</span>
            </span>
          </div>
        }

        @if (chunk.timeline?.length) {
        <div class="timeline-section">
          <div class="timeline-label">
            <mat-icon class="timeline-label-icon">timeline</mat-icon>
            <span class="timeline-label-text">
              {{ mode === 'search' ? 'Chunk timeline' : 'Jump to segment' }}
            </span>
          </div>
          <div class="timeline-track">
            @for (seg of chunk.timeline; track seg.source) {
              <button
                type="button"
                class="timeline-seg"
                [style.flex]="segmentFlex(seg)"
                [class.search-match]="mode === 'search' && seg.is_search_match"
                [class.query-hit]="mode === 'search' && seg.query_highlight"
                [class.best-match]="mode === 'search' && seg.is_best_match"
                (click)="onSegmentClick($event, seg.segment_start_sec)"
                [matTooltip]="segmentTooltip(seg)"
                matTooltipShowDelay="400">
                @if (mode === 'search' && seg.is_best_match) {
                  <mat-icon class="seg-pin">place</mat-icon>
                } @else if (mode === 'explore') {
                  <span class="seg-num">{{ seg.segment_number }}</span>
                }
              </button>
            }
          </div>
          @if (mode === 'search') {
            <div class="timeline-legend">
              <span class="legend-item"><span class="dot best"></span> Best match</span>
              <span class="legend-item"><span class="dot hit"></span> Query / match</span>
              @if (chunk.matched_segment_count > 1) {
                <span class="legend-item legend-hint">{{ chunk.matched_segment_count }} moments</span>
              }
            </div>
          }
        </div>
        }

        <div class="reasoning-content" [class.expanded]="isExpanded">
          <mat-icon class="reasoning-icon">psychology</mat-icon>
          <p [innerHTML]="captionHtml"></p>
        </div>
        @if (captionNeedsExpand()) {
          <button type="button" class="expand-toggle" (click)="toggleExpand($event)">
            <mat-icon>{{ isExpanded ? 'expand_less' : 'expand_more' }}</mat-icon>
            {{ isExpanded ? 'See less' : 'See more' }}
          </button>
        }

        <div class="object-tags">
          @for (obj of objectTags(); track obj) {
            <span class="object-chip" [class.highlight]="isQueryTerm(obj)">{{ obj }}</span>
          }
        </div>

        <div class="video-metadata">
          @if (chunk.camera_id?.trim()) {
            <span class="metadata-item"><mat-icon>videocam</mat-icon>{{ chunk.camera_id }}</span>
          }
          @if (chunk.capture_type?.trim()) {
            <span class="metadata-item"><mat-icon>category</mat-icon>{{ chunk.capture_type }}</span>
          }
          @if (chunk.location?.trim()) {
            <span class="metadata-item"><mat-icon>location_on</mat-icon>{{ chunk.location }}</span>
          }
          <span class="metadata-item">
            <mat-icon>{{ chunk.is_public ? 'public' : 'lock' }}</mat-icon>
            {{ chunk.is_public ? 'Public' : 'Private' }}
          </span>
        </div>

        @if (chunk.tags?.length) {
          <div class="tag-chips">
            @for (tag of chunk.tags; track tag) {
              <span class="tag-chip">{{ tag }}</span>
            }
          </div>
        }

        @if (mode === 'explore') {
        <div class="card-actions">
          <button mat-raised-button color="primary" class="action-btn" (click)="onOpen($event)">
            <mat-icon>play_arrow</mat-icon>
            {{ explorePlayLabel() }}
          </button>
          <button mat-stroked-button class="action-btn action-secondary" (click)="onSummarize($event)">
            <mat-icon>auto_awesome</mat-icon>
            Summarize
          </button>
          <button
            mat-icon-button
            type="button"
            class="action-delete"
            (click)="onDelete($event)"
            [disabled]="deleting"
            matTooltip="Delete this video">
            <mat-icon>delete</mat-icon>
          </button>
        </div>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .chunk-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      cursor: default;
      transition: all 0.3s ease;
      overflow: hidden;

      &:hover {
        transform: translateY(-4px);
        box-shadow: var(--shadow-hover);
        border-color: rgba(34, 197, 94, 0.45);
      }
    }

    .chunk-card mat-card-content,
    .chunk-card .title-row,
    .chunk-card .match-line,
    .chunk-card .video-metadata,
    .chunk-card .object-tags,
    .chunk-card .tag-chips {
      cursor: default;
    }

    .video-preview-container {
      position: relative;
      height: 200px;
      background: #000;
      overflow: hidden;
      cursor: pointer !important;
    }

    .video-preview-container * {
      cursor: pointer !important;
    }

    .preview-loading {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.45);
      pointer-events: none;
      z-index: 2;
    }

    .video-preview {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .play-overlay {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.35);
      pointer-events: none;
      transition: opacity 0.25s;

      &.hidden { opacity: 0; }

      mat-icon {
        font-size: 3.5rem;
        width: 3.5rem;
        height: 3.5rem;
        color: #fff;
      }
    }

    .upload-badge {
      position: absolute;
      left: 10px;
      bottom: 10px;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      padding: 0.3rem 0.55rem;
      border-radius: 8px;
      background: rgba(0, 0, 0, 0.72);
      color: #a5f3fc;
      font-size: 0.72rem;
      font-weight: 600;
      border: 1px solid rgba(115, 200, 253, 0.35);
      max-width: calc(100% - 20px);

      mat-icon {
        font-size: 0.9rem;
        width: 0.9rem;
        height: 0.9rem;
        flex-shrink: 0;
      }
    }

    .score-badge {
      position: absolute;
      top: 10px;
      right: 10px;
      padding: 0.3rem 0.55rem;
      border-radius: 8px;
      background: rgba(0, 0, 0, 0.72);
      color: #73c8fd;
      font-size: 0.75rem;
      font-weight: 600;
      border: 1px solid rgba(115, 200, 253, 0.35);
    }

    .title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 0.35rem;
    }

    .chunk-title {
      margin: 0;
      font-family: var(--font-name);
      font-size: 0.8rem;
      font-weight: 400;
      letter-spacing: 0.01em;
      color: var(--text-primary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }

    .chunk-title.explore-title {
      font-size: 1.024rem;
      font-weight: 400;
    }

    .duration-chip {
      font-size: 0.75rem;
      color: var(--text-secondary);
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 999px;
      padding: 0.15rem 0.55rem;
      flex-shrink: 0;
    }

    .match-line {
      margin: 0 0 0.75rem;
      font-size: 0.82rem;
      color: var(--text-secondary);

      strong { color: #22c55e; }
      .multi-hit { color: var(--text-muted); }

    }

    .explore-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin: 0 0 0.75rem;
    }

    .meta-badge {
      display: inline-flex;
      align-items: baseline;
      gap: 0.4rem;
      padding: 0.28rem 0.55rem 0.24rem;
      border-radius: 7px;
      background:
        linear-gradient(180deg, rgba(115, 200, 253, 0.16), rgba(115, 200, 253, 0.04));
      border: 1px solid rgba(115, 200, 253, 0.32);
      box-shadow: inset 0 0 14px rgba(115, 200, 253, 0.06);

      &.time {
        background: linear-gradient(180deg, rgba(148, 163, 184, 0.16), rgba(148, 163, 184, 0.04));
        border-color: rgba(148, 163, 184, 0.32);
        box-shadow: none;
      }

      &.segs {
        background: linear-gradient(180deg, rgba(34, 197, 94, 0.16), rgba(34, 197, 94, 0.04));
        border-color: rgba(34, 197, 94, 0.35);
        box-shadow: inset 0 0 14px rgba(34, 197, 94, 0.05);
      }
    }

    .badge-k {
      font-family: var(--font-name);
      font-size: 0.62rem;
      font-weight: 500;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: rgba(186, 214, 232, 0.78);
    }

    .meta-badge.chunk .badge-k { color: #8fd4ff; }
    .meta-badge.segs .badge-k { color: #86efac; }

    .badge-v {
      font-family: var(--font-name);
      font-size: 0.95rem;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0.02em;
      color: #f4fbff;
    }

    .timeline-section {
      margin-bottom: 0.85rem;
    }

    :host ::ng-deep .timeline-label,
    :host ::ng-deep .timeline-label-text,
    :host ::ng-deep .timeline-label-icon {
      color: #22c55e !important;
    }

    .timeline-label {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 0.4rem;

      .timeline-label-icon {
        font-size: 0.95rem;
        width: 0.95rem;
        height: 0.95rem;
      }
    }

    .timeline-track {
      display: flex;
      gap: 3px;
      height: 28px;
      border-radius: 8px;
      overflow: hidden;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
    }

    .timeline-seg {
      position: relative;
      border: none;
      padding: 0;
      min-width: 8px;
      background: rgba(148, 163, 184, 0.25);
      cursor: pointer;
      transition: filter 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;

      &:hover {
        filter: brightness(1.35);
        transform: scaleY(1.14);
        z-index: 2;
        box-shadow:
          0 0 12px rgba(34, 197, 94, 0.55),
          inset 0 0 0 1px rgba(134, 239, 172, 0.75);
      }

      &:hover .seg-num {
        color: #bbf7d0;
        text-shadow: 0 0 8px rgba(34, 197, 94, 0.95);
      }

      &.search-match {
        background: rgba(59, 130, 246, 0.45);
      }

      &.query-hit {
        background: rgba(34, 197, 94, 0.55);
        box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.8);
      }

      &.best-match {
        background: linear-gradient(180deg, rgba(34, 197, 94, 0.95), rgba(22, 163, 74, 0.85));
        box-shadow: inset 0 -2px 0 rgba(255, 255, 255, 0.35);
      }

      .seg-pin {
        position: absolute;
        top: 2px;
        left: 50%;
        transform: translateX(-50%);
        font-size: 0.85rem;
        width: 0.85rem;
        height: 0.85rem;
        color: #052e16;
      }

      .seg-num {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.62rem;
        font-weight: 700;
        color: var(--text-secondary);
        transition: color 0.15s ease, text-shadow 0.15s ease;
      }
    }

    .timeline-legend {
      display: flex;
      gap: 0.75rem;
      margin-top: 0.35rem;
      font-size: 0.68rem;
      color: var(--text-muted);
    }

    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;

      &.legend-hint {
        margin-left: auto;
        font-style: italic;
      }
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 2px;
      display: inline-block;

      &.best { background: #22c55e; }
      &.hit { background: rgba(34, 197, 94, 0.55); }
    }

    .reasoning-content {
      display: flex;
      gap: 0.6rem;
      background: rgba(6, 255, 165, 0.05);
      border: 1px solid rgba(6, 255, 165, 0.18);
      border-radius: 10px;
      padding: 0.65rem;
      margin-bottom: 0.65rem;
      cursor: text !important;
      user-select: text;
      -webkit-user-select: text;

      .reasoning-icon {
        color: rgba(6, 255, 165, 0.85);
        flex-shrink: 0;
        cursor: default !important;
        user-select: none;
      }

      p {
        margin: 0;
        font-size: 0.85rem;
        line-height: 1.45;
        color: var(--text-secondary);
        cursor: text !important;
        user-select: text;
        -webkit-user-select: text;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;

        ::ng-deep .query-term-text {
          color: #22c55e;
          font-weight: 600;
          cursor: text !important;
        }
      }

      &.expanded p {
        -webkit-line-clamp: unset;
        display: block;
      }
    }

    .expand-toggle {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      background: transparent;
      border: none;
      color: rgba(6, 255, 165, 0.8);
      font-size: 0.8rem;
      cursor: pointer;
      padding: 0.25rem 0.5rem;
      margin: -0.35rem 0 0.65rem;
      transition: color 0.2s ease;

      mat-icon {
        font-size: 1.1rem;
        width: 1.1rem;
        height: 1.1rem;
      }

      &:hover {
        color: rgba(6, 255, 165, 1);
      }
    }

    .object-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-bottom: 0.65rem;
      min-height: 1.25rem;
    }

    .object-chip {
      font-size: 0.7rem;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      color: var(--text-secondary);

      &.highlight {
        color: #22c55e;
        font-weight: 600;
      }
    }

    .video-metadata {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-bottom: 0.75rem;

      .metadata-item {
        display: inline-flex;
        align-items: center;
        gap: 0.25rem;
        font-size: 0.75rem;
        color: var(--text-secondary);
        background: var(--bg-secondary);
        padding: 0.2rem 0.5rem;
        border-radius: 999px;

        mat-icon {
          font-size: 0.9rem;
          width: 0.9rem;
          height: 0.9rem;
        }
      }
    }

    .tag-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-bottom: 0.75rem;
    }

    .tag-chip {
      font-size: 0.7rem;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      background: rgba(115, 200, 253, 0.12);
      border: 1px solid rgba(115, 200, 253, 0.35);
      color: var(--accent-primary);
    }

    .card-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-top: 0.15rem;
    }

    .action-btn {
      flex: 1 1 auto;
      min-width: 0;

      mat-icon {
        margin-right: 0.15rem;
      }
    }

    .action-btn[mat-raised-button] {
      background: var(--button-bg-primary) !important;
      color: var(--button-text) !important;

      mat-icon {
        color: var(--button-text) !important;
      }
    }

    .action-secondary {
      flex: 0 1 auto;
      color: var(--accent-primary) !important;
      border-color: rgba(115, 200, 253, 0.5) !important;

      mat-icon {
        color: var(--accent-primary) !important;
      }
    }

    .stream-hop {
      flex: 0 0 auto;
      color: var(--accent-primary) !important;

      mat-icon {
        color: var(--accent-primary) !important;
      }
    }

    .action-delete {
      flex: 0 0 auto;
      color: #f87171 !important;

      mat-icon {
        color: #f87171 !important;
      }
    }
  `],
})
export class ChunkCardComponent implements OnChanges {
  private videoService = inject(VideoService);
  private sanitizer = inject(DomSanitizer);
  private appConfig = inject(BackendModelsService);

  @Input({ required: true }) chunk!: ChunkSearchResult;
  @Input() mode: 'search' | 'explore' = 'search';
  @Input() deleting = false;
  @Output() open = new EventEmitter<ChunkSearchResult>();
  @Output() jumpTo = new EventEmitter<{ chunk: ChunkSearchResult; seekSec: number }>();
  @Output() summarize = new EventEmitter<ChunkSearchResult>();
  @Output() deleteVideo = new EventEmitter<ChunkSearchResult>();
  @Output() openStreamChunk = new EventEmitter<{ chunk: ChunkSearchResult; chunkIndex: number }>();

  @ViewChild('videoElement') videoElement?: ElementRef<HTMLVideoElement>;

  previewUrl = '';
  isPlaying = false;
  previewLoading = false;
  previewFrameReady = false;
  isExpanded = false;
  captionHtml: SafeHtml = '';
  private queryTerms: string[] = [];
  private hoverAbort?: AbortController;

  ngOnChanges(changes: SimpleChanges) {
    if (changes['chunk']) {
      this.isExpanded = false;
      this.syncFromChunk();
    }
  }

  private syncFromChunk() {
    const token = localStorage.getItem('video_lab_token');
    const source =
      this.chunk.preview_source?.trim() ||
      this.chunk.timeline?.[0]?.source?.trim() ||
      '';
    this.previewFrameReady = false;
    if (token && source) {
      this.previewUrl = this.videoService.getStreamUrl(source, token);
    } else {
      this.previewUrl = '';
    }
    this.queryTerms = extractHighlightTerms(this.chunk.query);
    const text = previewCaption(this.chunk);
    this.captionHtml = this.sanitizer.bypassSecurityTrustHtml(
      text ? highlightQueryTerms(text, this.queryTerms) : '',
    );
  }

  cardCaption(): string {
    return previewCaption(this.chunk);
  }

  captionNeedsExpand(): boolean {
    return this.cardCaption().length > 160;
  }

  toggleExpand(event: Event) {
    event.stopPropagation();
    this.isExpanded = !this.isExpanded;
  }

  exploreChunkLabel(): string | null {
    const idx = this.chunk.chunk_index;
    if (idx == null || idx < 0) return null;
    const total = this.chunk.stream_chunk_total;
    if (total != null && total > 0) return `${idx + 1}/${total}`;
    return `${idx + 1}`;
  }

  exploreSegmentLabel(): string {
    const total = this.chunk.total_segments;
    const indexed = this.chunk.timeline?.length ?? 0;
    if (indexed > 0 && indexed < total) return `${indexed}/${total}`;
    return `${total}`;
  }

  exploreSegmentTip(): string {
    const total = this.chunk.total_segments;
    const indexed = this.chunk.timeline?.length ?? 0;
    if (indexed > 0 && indexed < total) {
      return `${indexed} of ${total} segments indexed`;
    }
    return total === 1 ? '1 segment' : `${total} segments`;
  }

  onStreamHop(event: Event, direction: 'prev' | 'next') {
    event.stopPropagation();
    const index = direction === 'prev' ? this.chunk.prev_chunk_index : this.chunk.next_chunk_index;
    if (index == null || !this.chunk.stream_id) return;
    this.openStreamChunk.emit({ chunk: this.chunk, chunkIndex: index });
  }

  onDelete(event?: Event) {
    event?.stopPropagation();
    if (this.deleting) return;
    this.deleteVideo.emit(this.chunk);
  }

  onOpen(event?: Event) {
    event?.stopPropagation();
    this.open.emit(this.chunk);
  }

  onSummarize(event: Event) {
    event.stopPropagation();
    this.summarize.emit(this.chunk);
  }

  formatUploadDate(ts: string): string {
    return formatUploadBadgeTime(ts, this.appConfig.displayTimezone());
  }

  uploadTooltip(): string {
    const full = formatAbsoluteTime(
      this.chunk.upload_timestamp,
      this.appConfig.displayTimezone(),
    );
    return full ? `Uploaded ${full}` : '';
  }

  onSegmentClick(event: Event, seekSec: number) {
    event.stopPropagation();
    this.jumpTo.emit({ chunk: this.chunk, seekSec });
  }

  segmentFlex(seg: ChunkSearchResult['timeline'][0]): string {
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

  segmentTooltip(seg: ChunkSearchResult['timeline'][0]): string {
    const parts = [
      `#${seg.segment_number} ${this.formatTime(seg.segment_start_sec)}–${this.formatTime(seg.segment_end_sec)}`,
    ];
    if (seg.object_classes?.trim()) parts.push(seg.object_classes);
    return parts.join(' · ');
  }

  objectTags(): string[] {
    return previewObjectTags(this.chunk, 8);
  }

  explorePlayLabel(): string {
    return formatExplorePlayLabel(this.chunk);
  }

  isQueryTerm(label: string): boolean {
    return objectMatchesQuery(label, this.queryTerms);
  }

  onPreviewFrameReady() {
    this.previewFrameReady = true;
  }

  onVideoLoaded() {
    if (this.previewLoading) {
      this.previewLoading = false;
    }
  }

  async onHoverStart() {
    const video = this.videoElement?.nativeElement;
    if (!video || !this.previewUrl) return;

    claimHoverPreview(video);

    this.hoverAbort?.abort();
    this.hoverAbort = new AbortController();
    const signal = this.hoverAbort.signal;

    this.previewLoading = true;
    const played = await playHoverPreview(video, this.previewUrl, { signal });
    if (!signal.aborted) {
      this.previewLoading = false;
      this.isPlaying = played;
      if (played) {
        claimHoverPreview(video);
      }
    }
  }

  onHoverEnd() {
    this.hoverAbort?.abort();
    this.hoverAbort = undefined;
    this.previewLoading = false;

    const video = this.videoElement?.nativeElement;
    if (!video) return;
    stopHoverPreview(video);
    releaseHoverPreview(video);
    this.isPlaying = false;
  }
}
