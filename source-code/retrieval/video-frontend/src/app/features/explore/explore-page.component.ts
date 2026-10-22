import { Component, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription } from 'rxjs';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { ScopePillsComponent } from '../../shared/components/scope-pills.component';
import { ChunkCardComponent } from '../search/components/chunk-card.component';
import { PillRailComponent } from './components/pill-rail.component';
import { ExploreService } from './services/explore.service';
import { ChunkSearchResult, ExploreUploadDay, VideoScope } from '../../shared/models/video.model';
import { VideoPlayerComponent } from '../player/video-player.component';
import { VideoSummarizeDialogComponent } from './components/video-summarize-dialog.component';
import { UploadDialogComponent } from '../upload/upload-dialog.component';
import { formatAbsoluteTime } from '../../shared/utils/time.util';
import { BackendModelsService } from '../../shared/services/backend-models.service';
import {
  friendlyVastDbAccessMessage,
  resolveApiAccessWarning,
} from '../../shared/utils/api-access.util';
import { PageRefreshService } from '../../shared/services/page-refresh.service';

@Component({
  selector: 'app-explore-page',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    ScopePillsComponent,
    ChunkCardComponent,
    PillRailComponent,
  ],
  template: `
    <div class="explore-container">
      <section class="hero-card">
        <div class="hero-copy">
          <div class="hero-badge">
            <mat-icon>explore</mat-icon>
            Timeline browse
          </div>
          <h1>Browse by upload date</h1>
          <p>Indexed clips, day by day. Summarize any video on demand.</p>
        </div>
        <button type="button" class="upload-pill-button" (click)="openUpload()" matTooltip="Upload video">
          <mat-icon>cloud_upload</mat-icon>
          <span class="pill-label">Upload</span>
        </button>
      </section>

      <section class="controls-card">
        <div class="browse-row">
          <app-scope-pills
            label="Browse in:"
            [scope]="scope()"
            (scopeChange)="onScopeChange($event)">
          </app-scope-pills>
          <div class="index-switch">
            <span class="date-label">Index:</span>
            <div class="date-pills">
              <button
                type="button"
                class="date-pill"
                [class.active]="indexed() === 'complete'"
                (click)="selectIndexed('complete')">
                <mat-icon>check_circle</mat-icon>
                <span>Complete</span>
              </button>
              <button
                type="button"
                class="date-pill"
                [class.active]="indexed() === 'partial'"
                (click)="selectIndexed('partial')"
                matTooltip="Videos that have some segments indexed and are still missing others">
                <mat-icon>hourglass_top</mat-icon>
                <span>Incomplete</span>
              </button>
            </div>
          </div>
        </div>

        <app-pill-rail label="Upload day:">
          <button
            type="button"
            class="date-pill"
            [class.active]="!selectedDate()"
            (click)="selectDate(null)">
            <mat-icon>calendar_view_month</mat-icon>
            <span>All days</span>
            <span class="count">{{ totalVideos() }}</span>
          </button>
          @for (day of uploadsByDay(); track day.date) {
            <button
              type="button"
              class="date-pill"
              [class.active]="selectedDate() === day.date"
              (click)="selectDate(day.date)"
              [matTooltip]="formatDay(day.date)">
              <mat-icon>event</mat-icon>
              <span>{{ formatDayShort(day.date) }}</span>
              <span class="count">{{ day.chunk_count }}</span>
            </button>
          }
        </app-pill-rail>

        @if (locationOptions().length) {
          <app-pill-rail label="Location:">
            <button
              type="button"
              class="date-pill"
              [class.active]="!selectedLocation()"
              (click)="selectLocation(null)">
              <mat-icon>place</mat-icon>
              <span>All locations</span>
              <span class="count">{{ totalLocationVideos() }}</span>
            </button>
            @for (item of locationOptions(); track item.label) {
              <button
                type="button"
                class="date-pill"
                [class.active]="selectedLocation() === item.label"
                (click)="selectLocation(item.label)"
                [matTooltip]="item.label">
                <mat-icon>location_on</mat-icon>
                <span>{{ formatPillLabel(item.label) }}</span>
                <span class="count">{{ item.chunk_count }}</span>
              </button>
            }
          </app-pill-rail>
        }

        @if (cameraOptions().length) {
          <app-pill-rail label="Camera ID:">
            <button
              type="button"
              class="date-pill"
              [class.active]="!selectedCamera()"
              (click)="selectCamera(null)">
              <mat-icon>videocam</mat-icon>
              <span>All cameras</span>
              <span class="count">{{ totalCameraVideos() }}</span>
            </button>
            @for (item of cameraOptions(); track item.label) {
              <button
                type="button"
                class="date-pill"
                [class.active]="selectedCamera() === item.label"
                (click)="selectCamera(item.label)"
                [matTooltip]="item.label">
                <mat-icon>videocam</mat-icon>
                <span>{{ formatPillLabel(item.label) }}</span>
                <span class="count">{{ item.chunk_count }}</span>
              </button>
            }
          </app-pill-rail>
        }
      </section>

      @if (deleteError()) {
        <div class="access-warn-panel">
          <mat-icon>warning_amber</mat-icon>
          <div>
            <p>{{ deleteError() }}</p>
          </div>
        </div>
      }

      @if (accessWarning()) {
        <div class="access-warn-panel">
          <mat-icon>warning_amber</mat-icon>
          <div>
            <p>{{ accessWarning() }}</p>
            <span class="access-hint">Upload a video or confirm <code>vss-collection</code> exists in VastDB.</span>
          </div>
        </div>
      } @else if (tableInfoMessage() && !loading()) {
        <div class="info-banner">
          <mat-icon>info</mat-icon>
          <span>{{ tableInfoMessage() }}</span>
        </div>
      }

      @if (!accessWarning() && loading()) {
        <div class="loading-panel">
          <mat-spinner diameter="48"></mat-spinner>
          <span>Loading timeline…</span>
        </div>
      } @else if (!accessWarning() && !chunks().length) {
        <div class="empty-state">
          <img src="assets/vast_logo.svg" alt="VAST" class="vast-logo-glow">
          @if (indexed() === 'partial') {
            <h2>No incomplete videos</h2>
            <p>Every video in this view has all of its segments indexed.</p>
          } @else {
            <h2>No videos to explore</h2>
            <p>Upload videos or widen your browse scope to see indexed chunks here.</p>
          }
        </div>
      } @else if (!accessWarning()) {
        <div class="results-header">
          <h2>
            @if (selectedDate()) {
              {{ formatDay(selectedDate()!) }}
            } @else {
              All uploads
            }
            @if (selectedLocation()) {
              · {{ formatPillLabel(selectedLocation()!) }}
            }
            @if (selectedCamera()) {
              · {{ formatPillLabel(selectedCamera()!) }}
            }
          </h2>
          <div class="results-meta">
            <span>{{ total() }} video{{ total() === 1 ? '' : 's' }}</span>
            <span>Showing {{ pageStart() }}–{{ pageEnd() }}</span>
          </div>
        </div>

        <div class="results-grid">
          @for (chunk of chunks(); track trackChunk($index, chunk)) {
            <app-chunk-card
              mode="explore"
              [chunk]="chunk"
              [deleting]="deletingVideo() === chunk.original_video"
              (open)="openChunk($event)"
              (jumpTo)="openChunkAt($event.chunk, $event.seekSec)"
              (summarize)="summarizeChunk($event)"
              (deleteVideo)="deleteChunk($event)"
              (openStreamChunk)="openStreamChunk($event)">
            </app-chunk-card>
          }
        </div>

        @if (hasMultiplePages()) {
          <div class="pagination">
            <button mat-stroked-button [disabled]="offset() === 0" (click)="prevPage()">
              <mat-icon>chevron_left</mat-icon>
              Prev
            </button>
            <span class="page-indicator">Page {{ currentPage() }} / {{ totalPages() }}</span>
            <button mat-stroked-button [disabled]="!canGoNext()" (click)="nextPage()">
              Next
              <mat-icon>chevron_right</mat-icon>
            </button>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .explore-container {
      padding: 2rem;
      max-width: 1400px;
      margin: 0 auto;
      min-height: calc(100vh - 120px);
    }

    .hero-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.5rem;
      flex-wrap: wrap;
      background: linear-gradient(135deg, rgba(115, 200, 253, 0.12), rgba(6, 255, 165, 0.06));
      border: 1px solid var(--border-color);
      border-radius: 20px;
      padding: 1.75rem 2rem;
      margin-bottom: 1.5rem;
    }

    .hero-copy h1 {
      margin: 0.5rem 0 0.35rem;
      font-size: 1.85rem;
      color: var(--text-primary);
    }

    .hero-copy p {
      margin: 0;
      color: var(--text-secondary);
      max-width: 52ch;
    }

    .hero-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.35rem 0.75rem;
      border-radius: 999px;
      background: rgba(115, 200, 253, 0.15);
      border: 1px solid rgba(115, 200, 253, 0.35);
      color: var(--accent-primary);
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;

      mat-icon {
        font-size: 1rem;
        width: 1rem;
        height: 1rem;
      }
    }

    .upload-pill-button {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.24rem;
      min-width: 5.4rem;
      padding: 0.54rem 0.78rem;
      border: 1px solid var(--border-color);
      border-radius: 14px;
      background: var(--bg-secondary);
      color: var(--accent-primary);
      cursor: pointer;
      font-family: inherit;
      transition: background 0.2s ease, border-color 0.2s ease;

      mat-icon {
        font-size: 1.62rem;
        width: 1.62rem;
        height: 1.62rem;
      }

      .pill-label {
        font-size: 0.78rem;
        font-weight: 600;
      }

      &:hover {
        background: rgba(115, 200, 253, 0.15);
        border-color: var(--border-hover);
      }
    }

    .controls-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      padding: 1.5rem 1.75rem;
      margin-bottom: 1.75rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }

    .browse-row {
      display: flex;
      align-items: center;
      gap: 1.5rem;
      flex-wrap: wrap;
    }

    .index-switch {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }

    .index-switch .date-label {
      padding-top: 0;
    }

    .date-label {
      color: var(--text-secondary);
      font-size: 0.95rem;
      font-weight: 500;
      padding-top: 0.55rem;
      white-space: nowrap;
    }

    .date-pills {
      display: flex;
      gap: 0.5rem;
      flex-wrap: nowrap;
    }

    .date-pill {
      display: inline-flex;
      align-items: center;
      flex: 0 0 auto;
      white-space: nowrap;
      gap: 0.4rem;
      padding: 0.5rem 0.9rem;
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 20px;
      color: var(--text-secondary);
      font-size: 0.85rem;
      font-family: 'Roboto', sans-serif;
      cursor: pointer;
      transition: all 0.2s ease;

      mat-icon {
        font-size: 1rem;
        width: 1rem;
        height: 1rem;
        color: var(--accent-primary);
      }

      .count {
        font-size: 0.72rem;
        font-weight: 700;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        background: var(--bg-secondary);
        color: var(--text-muted);
      }

      &.active {
        background: var(--color-lightblue-400);
        border-color: var(--color-lightblue-400);
        color: var(--color-blue-1000);
        box-shadow: var(--shadow);

        mat-icon,
        .count {
          color: var(--color-blue-1000);
        }

        .count {
          background: rgba(14, 26, 53, 0.12);
        }
      }

      &:hover:not(.active) {
        background: var(--bg-card-hover);
        border-color: var(--border-hover);
        color: var(--text-primary);
      }
    }

    .error-banner {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 1rem 1.25rem;
      border-radius: 12px;
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
      margin-bottom: 1.5rem;
    }

    .access-warn-panel {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      margin-bottom: 1.5rem;
      padding: 1.25rem 1.5rem;
      border: 1px dashed rgba(232, 175, 111, 0.45);
      border-radius: 20px;
      background: var(--bg-card);
      color: var(--text-secondary);

      mat-icon {
        color: var(--accent-warning, #e8af6f);
        flex-shrink: 0;
      }

      p {
        margin: 0 0 0.35rem;
        color: var(--text-primary);
        font-weight: 500;
      }

      .access-hint {
        font-size: 0.9rem;
        color: var(--text-muted);

        code {
          color: var(--accent-primary);
          font-size: 0.82rem;
        }
      }
    }

    .info-banner {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      margin-bottom: 1.5rem;
      padding: 1rem 1.25rem;
      border: 1px solid rgba(115, 200, 253, 0.35);
      border-radius: 12px;
      background: rgba(115, 200, 253, 0.08);
      color: var(--text-secondary);
      font-size: 0.9rem;

      mat-icon {
        color: var(--accent-primary);
        flex-shrink: 0;
      }
    }

    .loading-panel,
    .empty-state {
      text-align: center;
      padding: 4rem 2rem;
      background: var(--bg-card);
      border: 1px dashed var(--border-color);
      border-radius: 20px;
      color: var(--text-secondary);
    }

    .loading-panel {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1rem;
    }

    .empty-state .vast-logo-glow {
      height: 30px;
      margin-bottom: 1.5rem;
      filter: brightness(0) invert(1) drop-shadow(0 0 15px rgba(0, 217, 255, 0.7));
    }

    .empty-state h2 {
      color: var(--text-primary);
      margin: 0 0 0.5rem;
    }

    .results-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: 1.25rem;
      padding-bottom: 0.75rem;
      border-bottom: 1px solid var(--border-color);

      h2 {
        margin: 0;
        font-size: 1.35rem;
        color: var(--text-primary);
      }
    }

    .results-meta {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 0.15rem;
      font-size: 0.88rem;
      color: var(--text-secondary);
    }

    .results-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
      gap: 1.5rem;
      animation: fadeIn 0.4s ease-out;
    }

    .pagination {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 1rem;
      margin-top: 1.75rem;

      button {
        color: var(--text-primary) !important;
        border-color: var(--border-color) !important;

        mat-icon {
          color: var(--text-primary) !important;
        }

        &:disabled {
          color: var(--text-muted) !important;
          opacity: 0.55;

          mat-icon {
            color: var(--text-muted) !important;
          }
        }
      }
    }

    .page-indicator {
      color: var(--text-primary);
      font-size: 0.9rem;
    }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
  `],
})
export class ExplorePageComponent implements OnInit, OnDestroy {
  private explore = inject(ExploreService);
  private dialog = inject(MatDialog);
  private pageRefresh = inject(PageRefreshService);
  private appConfig = inject(BackendModelsService);
  private uploadDialogRef: MatDialogRef<UploadDialogComponent> | null = null;
  private pageRefreshSub?: Subscription;

  private static readonly PAGE_SIZE = 24;

  scope = signal<VideoScope>('all');
  indexed = signal<'complete' | 'partial'>('complete');
  selectedDate = signal<string | null>(null);
  selectedLocation = signal<string | null>(null);
  selectedCamera = signal<string | null>(null);
  locationOptions = signal<{ label: string; chunk_count: number }[]>([]);
  cameraOptions = signal<{ label: string; chunk_count: number }[]>([]);
  chunks = signal<ChunkSearchResult[]>([]);
  uploadsByDay = signal<ExploreUploadDay[]>([]);
  total = signal(0);
  offset = signal(0);
  loading = signal(true);
  deletingVideo = signal<string | null>(null);
  streamHopKey = signal<string | null>(null);
  deleteError = signal<string | null>(null);
  accessWarning = signal<string | null>(null);
  tableInfoMessage = signal<string | null>(null);

  ngOnInit() {
    void this.appConfig.ensureLoaded();
    this.load();
    this.pageRefreshSub = this.pageRefresh.refresh$.subscribe(() => this.reloadView());
  }

  ngOnDestroy() {
    this.pageRefreshSub?.unsubscribe();
  }

  private reloadView() {
    this.offset.set(0);
    this.load();
  }

  totalVideos(): number {
    return this.uploadsByDay().reduce((sum, d) => sum + d.chunk_count, 0);
  }

  totalLocationVideos(): number {
    return this.locationOptions().reduce((sum, item) => sum + item.chunk_count, 0);
  }

  totalCameraVideos(): number {
    return this.cameraOptions().reduce((sum, item) => sum + item.chunk_count, 0);
  }

  onScopeChange(next: VideoScope) {
    this.scope.set(next);
    this.offset.set(0);
    this.load();
  }

  selectIndexed(next: 'complete' | 'partial') {
    if (this.indexed() === next) return;
    this.indexed.set(next);
    this.offset.set(0);
    this.load();
  }

  selectDate(date: string | null) {
    this.selectedDate.set(date);
    this.offset.set(0);
    this.load();
  }

  selectLocation(location: string | null) {
    this.selectedLocation.set(location);
    this.offset.set(0);
    this.load();
  }

  selectCamera(cameraId: string | null) {
    this.selectedCamera.set(cameraId);
    this.offset.set(0);
    this.load();
  }

  load() {
    this.loading.set(true);
    this.accessWarning.set(null);
    this.tableInfoMessage.set(null);
    this.explore
      .explore({
        scope: this.scope(),
        date: this.selectedDate(),
        location: this.selectedLocation(),
        cameraId: this.selectedCamera(),
        indexed: this.indexed(),
        limit: ExplorePageComponent.PAGE_SIZE,
        offset: this.offset(),
      })
      .subscribe({
        next: (res) => {
          this.chunks.set(res.chunks);
          this.uploadsByDay.set(res.uploads_by_day);
          if (res.locations?.length) {
            this.locationOptions.set(res.locations);
          } else if (!this.selectedLocation()) {
            this.locationOptions.set([]);
          }
          if (res.cameras?.length) {
            this.cameraOptions.set(res.cameras);
          } else if (!this.selectedCamera()) {
            this.cameraOptions.set([]);
          }
          this.total.set(res.total);
          this.loading.set(false);
          if (res.table_available === false) {
            this.accessWarning.set(
              res.table_message?.trim() || friendlyVastDbAccessMessage('explore'),
            );
            this.tableInfoMessage.set(null);
          } else if (res.table_message?.trim()) {
            this.tableInfoMessage.set(res.table_message.trim());
          }
        },
        error: (err) => {
          this.loading.set(false);
          this.chunks.set([]);
          this.uploadsByDay.set([]);
          this.total.set(0);
          this.tableInfoMessage.set(null);
          this.accessWarning.set(resolveApiAccessWarning(err, 'explore'));
        },
      });
  }

  formatDay(iso: string): string {
    return formatAbsoluteTime(iso + 'T12:00:00', this.appConfig.displayTimezone());
  }

  formatDayShort(iso: string): string {
    const date = new Date(iso + 'T12:00:00Z');
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      timeZone: this.appConfig.displayTimezone(),
    });
  }

  formatPillLabel(value: string): string {
    const text = (value || '').trim();
    if (!text || text === '(empty)') return 'Not set';
    if (text.length <= 36) return text;
    return text.slice(0, 35) + '…';
  }

  trackChunk(_i: number, chunk: ChunkSearchResult): string {
    return chunk.original_video;
  }

  pageStart(): number {
    if (!this.total()) return 0;
    return this.offset() + 1;
  }

  pageEnd(): number {
    return Math.min(this.offset() + this.chunks().length, this.total());
  }

  currentPage(): number {
    return Math.floor(this.offset() / ExplorePageComponent.PAGE_SIZE) + 1;
  }

  totalPages(): number {
    return Math.max(1, Math.ceil(this.total() / ExplorePageComponent.PAGE_SIZE));
  }

  hasMultiplePages(): boolean {
    return this.totalPages() > 1;
  }

  canGoNext(): boolean {
    return this.offset() + ExplorePageComponent.PAGE_SIZE < this.total();
  }

  prevPage() {
    if (this.offset() === 0) return;
    this.offset.update((o) => Math.max(0, o - ExplorePageComponent.PAGE_SIZE));
    this.load();
  }

  nextPage() {
    if (!this.canGoNext()) return;
    this.offset.update((o) => o + ExplorePageComponent.PAGE_SIZE);
    this.load();
  }

  openChunk(chunk: ChunkSearchResult, seekSec?: number) {
    this.dialog.open(VideoPlayerComponent, {
      data: {
        chunk,
        query: '',
        mode: 'explore',
        scope: this.scope(),
        initialSeekSec: seekSec ?? 0,
      },
      width: '92vw',
      maxWidth: '1100px',
      height: '92vh',
      panelClass: 'video-player-dialog',
    });
  }

  openStreamChunk(event: { chunk: ChunkSearchResult; chunkIndex: number }) {
    const streamId = event.chunk.stream_id;
    if (!streamId || this.streamHopKey()) return;
    this.streamHopKey.set(`${streamId}:${event.chunkIndex}`);
    this.explore.getStreamChunk(streamId, event.chunkIndex, this.scope()).subscribe({
      next: (next) => {
        this.streamHopKey.set(null);
        this.openChunk(next, 0);
      },
      error: () => {
        this.streamHopKey.set(null);
        this.deleteError.set('Could not open that stream chunk.');
      },
    });
  }

  openChunkAt(chunk: ChunkSearchResult, seekSec: number) {
    this.openChunk(chunk, seekSec);
  }

  deleteChunk(chunk: ChunkSearchResult) {
    if (this.deletingVideo()) return;
    const name = chunk.filename || 'this video';
    const confirmed = confirm(
      `Delete ${name}? This removes the stored video, its segments, detection files, and index rows.`,
    );
    if (!confirmed) return;
    this.deleteError.set(null);
    this.deletingVideo.set(chunk.original_video);
    this.explore.deleteVideo(chunk.original_video).subscribe({
      next: (res) => {
        this.deletingVideo.set(null);
        if (res.object_errors?.length) {
          this.deleteError.set(
            `Deleted the index, but some files could not be removed: ${res.object_errors.join('; ')}`,
          );
        }
        this.load();
      },
      error: (err) => {
        this.deletingVideo.set(null);
        const detail = err?.error?.detail;
        this.deleteError.set(typeof detail === 'string' ? detail : 'Delete failed');
      },
    });
  }

  summarizeChunk(chunk: ChunkSearchResult) {
    this.dialog.open(VideoSummarizeDialogComponent, {
      width: '720px',
      maxWidth: '95vw',
      maxHeight: '90vh',
      panelClass: 'video-summarize-dialog',
      data: { chunk },
    });
  }

  openUpload() {
    this.uploadDialogRef = this.dialog.open(UploadDialogComponent, {
      width: '600px',
      maxWidth: '95vw',
      maxHeight: '85vh',
      panelClass: 'upload-dialog',
    });
    this.uploadDialogRef.afterClosed().subscribe(() => {
      this.uploadDialogRef = null;
      this.load();
    });
  }
}
