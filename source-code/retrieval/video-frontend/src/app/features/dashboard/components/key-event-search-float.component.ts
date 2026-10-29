import {
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { HttpClient } from '@angular/common/http';
import { DomSanitizer, SafeHtml, SafeResourceUrl } from '@angular/platform-browser';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { KeyEventSuggestion } from '../../../shared/models/suggestions.model';
import { VideoSearchResult } from '../../../shared/models/video.model';
import { VideoService } from '../../../shared/services/video.service';
import { playVideoMuted } from '../../../shared/utils/video-hover-preview.util';
import {
  extractHighlightTerms,
  highlightQueryTerms,
} from '../../../shared/utils/query-highlight.util';

type FloatPhase = 'idle' | 'embedding' | 'searching' | 'player' | 'error';

@Component({
  selector: 'app-key-event-search-float',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  template: `
    @if (phase() !== 'idle') {
      <div class="float-backdrop" (click)="close()">
        @if (phase() === 'embedding' || phase() === 'searching') {
          <div class="mini-search-card" (click)="$event.stopPropagation()">
            <div class="mini-phase" [class.active]="phase() === 'embedding'" [class.done]="phase() !== 'embedding'">
              <mat-icon>psychology</mat-icon>
              <div>
                <strong>Embedding query</strong>
                <span>Cosmos Embed · hybrid vector</span>
              </div>
              @if (phase() === 'embedding') {
                <div class="dots"><span></span><span></span><span></span></div>
              } @else {
                <mat-icon class="done-icon">check_circle</mat-icon>
              }
            </div>
            <div class="mini-phase" [class.active]="phase() === 'searching'">
              <mat-icon>search</mat-icon>
              <div>
                <strong>Searching VastDB</strong>
                <span>Finding best segment match</span>
              </div>
              @if (phase() === 'searching') {
                <div class="dots"><span></span><span></span><span></span></div>
              }
            </div>
            <p class="mini-query">"{{ activeEvent()?.query_text }}"</p>
          </div>
        }

        @if (phase() === 'error') {
          <div class="float-panel error-panel" (click)="$event.stopPropagation()">
            <mat-icon>search_off</mat-icon>
            <p>{{ errorMessage() }}</p>
            <button mat-stroked-button type="button" (click)="close()">Close</button>
          </div>
        }

        @if (phase() === 'player' && currentSegment()) {
          <div
            class="float-panel player-panel"
            (click)="$event.stopPropagation()"
            (mouseenter)="panelHovered.set(true)"
            (mouseleave)="panelHovered.set(false)">
            <button
              type="button"
              class="float-close"
              [class.visible]="panelHovered()"
              (click)="close()"
              aria-label="Close">
              <mat-icon>close</mat-icon>
            </button>

            <header class="panel-header">
              <div class="header-text">
                <span class="event-label">{{ eventTitle() }}</span>
                <span class="segment-meta">
                  Segment {{ segmentIndex() + 1 }} / {{ segments().length }}
                  · {{ formatTime(currentSegment()!.segment_start_sec) }}–{{ formatTime(currentSegment()!.segment_end_sec) }}
                  @if (fromEventClip()) {
                    · Event clip
                  } @else {
                    · {{ matchPercent() }}% match
                  }
                </span>
              </div>
            </header>

            <div class="video-wrap">
              @if (loadingVideo()) {
                <div class="video-loading">
                  <mat-spinner diameter="36"></mat-spinner>
                </div>
              }
              @if (streamUrl() && !videoError()) {
                <video
                  #videoEl
                  [src]="streamUrl()"
                  class="segment-video"
                  controls
                  playsinline
                  muted
                  (loadeddata)="onVideoReady()"
                  (error)="onVideoError()">
                </video>
              }
              @if (videoError()) {
                <div class="video-error">
                  <mat-icon>error_outline</mat-icon>
                  <span>Could not load segment</span>
                </div>
              }
            </div>

            <p class="segment-caption" [innerHTML]="captionHtml()"></p>

            <footer class="panel-nav">
              <button
                mat-stroked-button
                class="nav-btn"
                type="button"
                [disabled]="!canGoPrev()"
                (click)="goPrev()">
                <mat-icon>chevron_left</mat-icon>
                Previous
              </button>
              <span class="filename" [matTooltip]="currentSegment()!.original_video">
                {{ truncate(currentSegment()!.filename, 36) }}
              </span>
              <button
                mat-stroked-button
                class="nav-btn"
                type="button"
                [disabled]="!canGoNext()"
                (click)="goNext()">
                Next
                <mat-icon>chevron_right</mat-icon>
              </button>
            </footer>
          </div>
        }
      </div>
    }
  `,
  styles: [`
    .float-backdrop {
      position: fixed;
      inset: 0;
      z-index: 10000;
      background: rgba(0, 0, 0, 0.55);
      backdrop-filter: blur(6px);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
      animation: fadeIn 0.2s ease;
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    .mini-search-card {
      width: min(420px, 92vw);
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 18px;
      padding: 1.25rem 1.35rem;
      box-shadow: var(--shadow-hover);
    }

    .mini-phase {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 0.85rem;
      border-radius: 12px;
      border: 1px solid var(--border-color);
      margin-bottom: 0.55rem;
      opacity: 0.45;
      transition: all 0.25s ease;

      &.active {
        opacity: 1;
        border-color: var(--accent-primary);
        background: rgba(115, 200, 253, 0.08);
      }

      &.done {
        opacity: 0.75;
        border-color: rgba(34, 197, 94, 0.45);
      }

      > mat-icon:first-child {
        color: var(--accent-primary);
      }

      div {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 0.1rem;

        strong {
          font-size: 0.92rem;
          color: var(--text-primary);
        }

        span {
          font-size: 0.78rem;
          color: var(--text-muted);
        }
      }

      .done-icon {
        color: #22c55e;
        font-size: 1.25rem;
        width: 1.25rem;
        height: 1.25rem;
      }
    }

    .dots {
      display: flex;
      gap: 0.3rem;

      span {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--accent-primary);
        animation: bounce 0.9s infinite;

        &:nth-child(2) { animation-delay: 0.15s; }
        &:nth-child(3) { animation-delay: 0.3s; }
      }
    }

    @keyframes bounce {
      0%, 100% { transform: translateY(0); opacity: 0.4; }
      50% { transform: translateY(-5px); opacity: 1; }
    }

    .mini-query {
      margin: 0.65rem 0 0;
      font-size: 0.82rem;
      color: var(--text-secondary);
      font-style: italic;
      text-align: center;
    }

    .float-panel {
      position: relative;
      width: min(560px, 94vw);
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 18px;
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
      overflow: hidden;
      animation: slideUp 0.28s ease;
    }

    @keyframes slideUp {
      from { opacity: 0; transform: translateY(16px) scale(0.98); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }

    .float-close {
      position: absolute;
      top: 0.55rem;
      right: 0.55rem;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border: none;
      border-radius: 50%;
      background: rgba(0, 0, 0, 0.65);
      color: #fff;
      cursor: pointer;
      opacity: 0;
      transform: scale(0.9);
      transition: opacity 0.2s ease, transform 0.2s ease;

      &.visible {
        opacity: 1;
        transform: scale(1);
      }

      mat-icon {
        font-size: 1.1rem;
        width: 1.1rem;
        height: 1.1rem;
      }
    }

    .panel-header {
      padding: 1rem 1.1rem 0.65rem;
      border-bottom: 1px solid var(--border-color);
    }

    .event-label {
      display: block;
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      padding-right: 2rem;
    }

    .segment-meta {
      display: block;
      margin-top: 0.25rem;
      font-size: 0.78rem;
      color: var(--text-muted);
    }

    .video-wrap {
      position: relative;
      background: #000;
      min-height: 200px;
    }

    .segment-video {
      width: 100%;
      max-height: 42vh;
      display: block;
      object-fit: contain;
    }

    .video-loading,
    .video-error {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      color: var(--text-secondary);
      min-height: 200px;
    }

    .segment-caption {
      margin: 0;
      padding: 0.85rem 1.1rem;
      font-size: 0.88rem;
      line-height: 1.5;
      color: var(--text-secondary);
      border-bottom: 1px solid var(--border-color);
      max-height: 120px;
      overflow-y: auto;
      cursor: text;
      user-select: text;
      -webkit-user-select: text;

      ::ng-deep .query-term-text {
        color: #22c55e;
        font-weight: 600;
      }
    }

    .panel-nav {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      padding: 0.75rem 1rem;

      .nav-btn {
        color: var(--text-primary) !important;
        border-color: var(--border-color) !important;
        background: transparent !important;

        mat-icon {
          color: inherit;
        }

        &:hover:not([disabled]) {
          color: #fff !important;
          border-color: rgba(255, 255, 255, 0.55) !important;
          background: rgba(255, 255, 255, 0.1) !important;

          mat-icon,
          .mdc-button__label {
            color: #fff !important;
          }
        }
      }
    }

    .filename {
      flex: 1;
      text-align: center;
      font-family: var(--font-name);
      font-size: 0.624rem;
      font-weight: 400;
      letter-spacing: 0.01em;
      color: var(--text-muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .error-panel {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.75rem;
      padding: 2rem 1.5rem;
      text-align: center;
      color: var(--text-secondary);

      mat-icon {
        font-size: 2.5rem;
        width: 2.5rem;
        height: 2.5rem;
        color: var(--accent-warning, #e8af6f);
      }
    }
  `],
})
export class KeyEventSearchFloatComponent implements OnChanges {
  private videoService = inject(VideoService);
  private http = inject(HttpClient);
  private sanitizer = inject(DomSanitizer);

  @Input() event: KeyEventSuggestion | null = null;
  @Output() closed = new EventEmitter<void>();

  @ViewChild('videoEl') videoEl?: ElementRef<HTMLVideoElement>;

  phase = signal<FloatPhase>('idle');
  activeEvent = signal<KeyEventSuggestion | null>(null);
  segments = signal<VideoSearchResult[]>([]);
  segmentIndex = signal(0);
  errorMessage = signal('');
  panelHovered = signal(false);
  loadingVideo = signal(true);
  videoError = signal(false);
  streamUrl = signal<SafeResourceUrl | null>(null);
  fromEventClip = signal(false);
  private queryTerms: string[] = [];

  ngOnChanges(changes: SimpleChanges) {
    if (!changes['event']) return;
    if (!this.event) {
      this.resetState(false);
      return;
    }
    this.runSearch(this.event);
  }

  @HostListener('document:keydown', ['$event'])
  onKeydown(event: KeyboardEvent) {
    if (this.phase() === 'idle') return;
    if (event.key === 'Escape') {
      this.close();
      return;
    }
    if (this.phase() !== 'player') return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      this.goPrev();
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      this.goNext();
    }
  }

  currentSegment(): VideoSearchResult | null {
    const list = this.segments();
    const idx = this.segmentIndex();
    return list[idx] ?? null;
  }

  eventTitle(): string {
    const ev = this.activeEvent();
    if (!ev) return '';
    const label = (ev.label || '').trim();
    const query = (ev.query_text || '').trim();
    if (label && label.toLowerCase() !== query.toLowerCase()) return label;
    return query;
  }

  matchPercent(): string {
    const seg = this.currentSegment();
    if (!seg) return '0';
    return (seg.similarity_score * 100).toFixed(0);
  }

  captionHtml(): SafeHtml {
    const seg = this.currentSegment();
    if (!seg) return '';
    const text = seg.reasoning_content || '';
    const html = highlightQueryTerms(text, this.queryTerms);
    return this.sanitizer.bypassSecurityTrustHtml(html);
  }

  canGoPrev(): boolean {
    return this.segmentIndex() > 0;
  }

  canGoNext(): boolean {
    return this.segmentIndex() < this.segments().length - 1;
  }

  goPrev() {
    if (!this.canGoPrev()) return;
    this.segmentIndex.update((i) => i - 1);
    this.loadCurrentSegment();
  }

  goNext() {
    if (!this.canGoNext()) return;
    this.segmentIndex.update((i) => i + 1);
    this.loadCurrentSegment();
  }

  close() {
    this.resetState(true);
  }

  private resetState(emitClosed: boolean) {
    this.phase.set('idle');
    this.activeEvent.set(null);
    this.segments.set([]);
    this.segmentIndex.set(0);
    this.streamUrl.set(null);
    this.panelHovered.set(false);
    this.fromEventClip.set(false);
    if (emitClosed) {
      this.closed.emit();
    }
  }

  formatTime(sec: number): string {
    if (!Number.isFinite(sec) || sec < 0) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  truncate(text: string, max: number): string {
    if (!text || text.length <= max) return text;
    return text.slice(0, max - 1) + '…';
  }

  onVideoReady() {
    this.loadingVideo.set(false);
    const el = this.videoEl?.nativeElement;
    if (el) {
      el.currentTime = 0;
      void playVideoMuted(el).catch(() => undefined);
    }
  }

  onVideoError() {
    this.loadingVideo.set(false);
    this.videoError.set(true);
  }

  private async runSearch(ev: KeyEventSuggestion) {
    const query = ev.query_text?.trim();
    if (!query) return;

    this.activeEvent.set(ev);
    this.phase.set('embedding');
    this.errorMessage.set('');
    this.fromEventClip.set(false);
    this.queryTerms = extractHighlightTerms(query);

    await this.delay(320);
    if (await this.tryLoadFromEventMetadata(ev)) {
      return;
    }

    const searchPromise = firstValueFrom(
      this.videoService.search({
        query,
        top_k: 20,
        llm_top_n: 1,
        include_public: true,
      }),
    );

    this.phase.set('searching');

    try {
      const response = await searchPromise;
      await this.delay(280);

      let ranked = this.rankSegments(response.results ?? [], ev);
      if (ev.original_video?.trim()) {
        const ov = ev.original_video.trim();
        const onVideo = ranked.filter((r) => r.original_video === ov);
        if (onVideo.length) {
          ranked = onVideo;
        }
      }
      if (!ranked.length) {
        this.errorMessage.set('No matching segments found for this event.');
        this.phase.set('error');
        return;
      }

      this.segments.set(ranked);
      this.segmentIndex.set(0);
      this.phase.set('player');
      this.loadCurrentSegment();
    } catch {
      this.errorMessage.set('Search failed. Check backend connectivity and try again.');
      this.phase.set('error');
    }
  }

  private async tryLoadFromEventMetadata(ev: KeyEventSuggestion): Promise<boolean> {
    const ov = ev.original_video?.trim();
    if (!ov) {
      return false;
    }

    try {
      const resp = await firstValueFrom(
        this.http.get<{ segments: Record<string, unknown>[] }>(
          `${environment.apiUrl}/tools/segments`,
          { params: { original_video: ov } },
        ),
      );
      const mapped = (resp.segments ?? []).map((s) => this.segmentDictToResult(s, ev));
      if (!mapped.length) {
        return false;
      }

      this.fromEventClip.set(true);
      this.segments.set(mapped);
      this.segmentIndex.set(this.pickSegmentIndex(mapped, ev));
      this.phase.set('player');
      this.loadCurrentSegment();
      return true;
    } catch {
      return false;
    }
  }

  private segmentDictToResult(
    s: Record<string, unknown>,
    ev: KeyEventSuggestion,
  ): VideoSearchResult {
    return {
      filename: String(s['filename'] ?? ev.filename ?? ''),
      source: String(s['source'] ?? ''),
      reasoning_content: String(s['reasoning_content'] ?? ''),
      is_public: Boolean(s['is_public']),
      upload_timestamp: String(s['upload_timestamp'] ?? ev.upload_timestamp ?? ''),
      duration: Number(s['duration'] ?? 0),
      segment_number: Number(s['segment_number'] ?? 0),
      total_segments: Number(s['total_segments'] ?? 0),
      segment_start_sec: Number(s['segment_start_sec'] ?? 0),
      segment_end_sec: Number(s['segment_end_sec'] ?? 0),
      original_video: String(s['original_video'] ?? ev.original_video ?? ''),
      tags: Array.isArray(s['tags']) ? s['tags'].map(String) : [],
      similarity_score: 1,
      cosmos_model: s['cosmos_model'] ? String(s['cosmos_model']) : undefined,
      camera_id: s['camera_id'] ? String(s['camera_id']) : undefined,
      capture_type: s['capture_type'] ? String(s['capture_type']) : undefined,
      location: s['location'] ? String(s['location']) : undefined,
    };
  }

  private pickSegmentIndex(segments: VideoSearchResult[], ev: KeyEventSuggestion): number {
    const evStart = ev.segment_start_sec ?? 0;
    const evEnd = ev.segment_end_sec ?? evStart;
    let best = 0;
    let bestScore = Number.NEGATIVE_INFINITY;
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const overlap =
        Math.min(seg.segment_end_sec, evEnd) - Math.max(seg.segment_start_sec, evStart);
      const score = overlap > 0 ? overlap + 1000 : -Math.abs(seg.segment_start_sec - evStart);
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    }
    return best;
  }

  private rankSegments(results: VideoSearchResult[], ev: KeyEventSuggestion): VideoSearchResult[] {
    const score = (r: VideoSearchResult) => {
      let s = r.similarity_score;
      if (r.original_video === ev.original_video) s += 1;
      const overlap =
        Math.min(r.segment_end_sec, ev.segment_end_sec) -
        Math.max(r.segment_start_sec, ev.segment_start_sec);
      if (r.original_video === ev.original_video && overlap > 0) s += 0.5;
      return s;
    };
    return [...results].sort((a, b) => score(b) - score(a));
  }

  private loadCurrentSegment() {
    const seg = this.currentSegment();
    if (!seg) return;

    this.loadingVideo.set(true);
    this.videoError.set(false);
    this.streamUrl.set(null);

    try {
      const token = localStorage.getItem('video_lab_token');
      if (!token) throw new Error('Not authenticated');
      const url = this.videoService.getStreamUrl(seg.source, token);
      this.streamUrl.set(this.sanitizer.bypassSecurityTrustResourceUrl(url));
    } catch {
      this.loadingVideo.set(false);
      this.videoError.set(true);
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
