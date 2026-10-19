import { Component, ViewChild, inject, signal, OnInit, OnDestroy, AfterViewInit, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Subscription, interval } from 'rxjs';
import { SearchBarComponent } from './components/search-bar.component';
import { ChunkCardComponent } from './components/chunk-card.component';
import { SearchAnimationComponent } from './components/search-animation.component';
import { LLMSynthesisComponent } from './components/llm-synthesis.component';
import { SearchService } from './services/search.service';
import { PageRefreshService } from '../../shared/services/page-refresh.service';
import { SearchRequest, ChunkSearchResult } from '../../shared/models/video.model';
import { VideoPlayerComponent } from '../player/video-player.component';
import { UploadDialogComponent } from '../upload/upload-dialog.component';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { AuthService } from '../auth/services/auth.service';
import { SuggestionsService } from '../../shared/services/suggestions.service';
import { BackendModelsService } from '../../shared/services/backend-models.service';
import { formatAbsoluteTime, formatRelativeTime } from '../../shared/utils/time.util';
import {
  friendlyVastDbAccessMessage,
  resolveApiAccessWarning,
} from '../../shared/utils/api-access.util';

@Component({
  selector: 'app-search-page',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    SearchBarComponent,
    ChunkCardComponent,
    SearchAnimationComponent,
    LLMSynthesisComponent
  ],
  template: `
    <div class="search-container">
      <app-search-bar (search)="onSearch($event)" (uploadClick)="openUpload()"></app-search-bar>

      @if (searchService.state().error) {
        <div class="error-message">
          <mat-icon>error_outline</mat-icon>
          <span>{{ searchService.state().error }}</span>
        </div>
      }

      @if (showSuggestions()) {
        <div class="empty-state">
          <img src="assets/vast_logo.svg" alt="VAST" class="vast-logo-glow">
          <h2>What are you looking for?</h2>
          <p>Type what you want to see in plain words to find the right clips</p>
          <div class="examples">
            <div class="examples-header">
              <h3>Try something like:</h3>
              @if (suggestionsUpdatedAt()) {
                <span
                  class="examples-updated"
                  [matTooltip]="formatSuggestionsUpdated(suggestionsUpdatedAt())">
                  Updated {{ suggestionsUpdatedRelative() }}
                </span>
              }
            </div>
            @if (suggestionsLoading()) {
              <div class="examples-status">
                <mat-spinner diameter="28"></mat-spinner>
                <span>Loading search suggestions…</span>
              </div>
            } @else if (suggestionsAccessWarning()) {
              <p class="examples-hint examples-hint-warn">
                <mat-icon>warning_amber</mat-icon>
                {{ suggestionsAccessWarning() }}
              </p>
            } @else if (!exampleQueries().length) {
              <p class="examples-hint">
                No suggestions yet. Run the <strong>prompt-suggester</strong> DataEngine function
                after segments are in <code>vss-collection</code> (writes to
                <code>vss-events-collection</code>).
              </p>
            } @else {
              <ul class="example-list">
                @for (example of exampleQueries(); track example) {
                  <li>
                    <button
                      type="button"
                      class="example-query-button"
                      (click)="onExampleQueryClick(example)">
                      <mat-icon>search</mat-icon>
                      <span class="example-query-text">"{{ example }}"</span>
                    </button>
                  </li>
                }
              </ul>
            }
          </div>
        </div>
      }

      @if (displayResults().length > 0) {
        <div class="results-header">
          <h2>Search Results</h2>
          <div class="results-info">
            <span>Found {{ displayResults().length }} clip{{ displayResults().length === 1 ? '' : 's' }}</span>
            <span>
              Showing {{ pageStartIndex() }}-{{ pageEndIndex() }} / {{ displayResults().length }}
            </span>
            @if (searchService.state().permissionFiltered > 0) {
              <span class="filtered-info">
                ({{ searchService.state().permissionFiltered }} filtered by permissions)
              </span>
            }
            <span class="timing-info">
              Embedding: {{ searchService.state().embeddingTimeMs.toFixed(0) }}ms | 
              Search: {{ searchService.state().searchTimeMs.toFixed(0) }}ms
            </span>
          </div>
        </div>

        <!-- LLM Synthesis (if available) -->
        @if (searchService.state().llmSynthesis) {
          <app-llm-synthesis [synthesis]="searchService.state().llmSynthesis"></app-llm-synthesis>
        }

        <div class="results-grid">
          @for (chunk of visibleChunks(); track trackChunk($index, chunk)) {
            <app-chunk-card
              [chunk]="chunk"
              (open)="openChunk($event)"
              (jumpTo)="openChunkAt($event.chunk, $event.seekSec)">
            </app-chunk-card>
          }
        </div>
        @if (hasMultiplePages()) {
          <div class="pagination-container">
            <button mat-stroked-button class="pagination-btn" [disabled]="!canGoPrev()" (click)="goPrevPage()">
              <mat-icon>chevron_left</mat-icon>
              Prev
            </button>
            <div class="page-numbers">
              @for (page of pageNumbers(); track page) {
                <button
                  mat-stroked-button
                  class="page-btn"
                  [class.active]="page === currentPage()"
                  (click)="goToPage(page)">
                  {{ page }}
                </button>
              }
            </div>
            <button mat-stroked-button class="pagination-btn" [disabled]="!canGoNext()" (click)="goNextPage()">
              Next
              <mat-icon>chevron_right</mat-icon>
            </button>
          </div>
        }
      }

      @if (hasSearched() && displayResults().length === 0 && !searchService.state().loading) {
        <div class="no-results">
          <img src="assets/vast_logo.svg" alt="VAST" class="vast-logo-glow">
          <h2>No videos found</h2>
          <p>Try a different query or check your filters</p>
        </div>
      }
    </div>

    <app-search-animation
      [phase]="searchService.state().animationPhase"
      [embeddingTime]="searchService.state().embeddingTimeMs"
      [searchTime]="searchService.state().searchTimeMs"
      [llmTime]="searchService.state().llmTimeMs"
      [resultsCount]="displayResults().length"
      [showLlmPhase]="searchService.state().expectSynthesis"
      [embeddingModelDetail]="modelsService.embeddingDetail()"
      [synthesisModel]="modelsService.synthesisLabel()"
      (close)="closeAnimation()">
    </app-search-animation>
  `,
  styles: [`
    .search-container {
      padding: 2rem;
      max-width: 1400px;
      margin: 0 auto;
      min-height: 100vh;
    }

    .error-message {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 12px;
      padding: 1rem;
      color: #fca5a5;
      margin-bottom: 2rem;
      
      mat-icon {
        color: #ef4444;
      }
    }

    .empty-state {
      text-align: center;
      padding: 4rem 2rem;
      background: var(--bg-card);
      border: 1px dashed var(--border-color);
      border-radius: 20px;
      transition: background 0.3s ease, border-color 0.3s ease;
      
      .vast-logo-glow {
        height: 30px;
        width: auto;
        margin-bottom: 2rem;
        filter: brightness(0) invert(1) drop-shadow(0 0 15px rgba(0, 217, 255, 0.7));
        animation: glow-pulse 1.2s ease-in-out infinite;
        transition: filter 0.3s ease;
      }
      
      [data-theme="light"] & .vast-logo-glow {
        filter: none drop-shadow(0 0 15px rgba(0, 206, 209, 0.5));
      }
      
      h2 {
        color: var(--text-primary);
        font-size: 2rem;
        margin-bottom: 0.5rem;
      }
      
      p {
        color: var(--text-secondary);
        font-size: 1.1rem;
        margin-bottom: 2rem;
      }
      
      .examples {
        text-align: left;
        max-width: 600px;
        margin: 0 auto;
        background: var(--bg-secondary);
        padding: 1.5rem;
        border-radius: 12px;
        border: 1px solid var(--border-color);
        transition: background 0.3s ease, border-color 0.3s ease;
        
        h3 {
          color: var(--accent-primary);
          margin: 0;
          font-size: 1rem;
        }

        .examples-header {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 1rem;
          margin-bottom: 0.75rem;
        }

        .examples-updated {
          font-size: 0.8rem;
          color: var(--text-muted);
          white-space: nowrap;
        }

        .examples-status {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          color: var(--text-secondary);
          padding: 0.5rem 0;
        }

        .examples-hint {
          margin: 0;
          color: var(--text-secondary);
          font-size: 0.9rem;
          line-height: 1.5;

          code {
            font-size: 0.82rem;
            color: var(--accent-primary);
          }
        }

        .examples-hint-warn {
          display: flex;
          align-items: flex-start;
          gap: 0.5rem;
          color: var(--accent-warning, #e8af6f);

          mat-icon {
            font-size: 1.1rem;
            width: 1.1rem;
            height: 1.1rem;
            flex-shrink: 0;
          }
        }
        
        ul.example-list {
          list-style: none;
          padding: 0;

          li {
            padding: 0.35rem 0;
          }

          .example-query-button {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            width: 100%;
            margin: 0;
            padding: 0.5rem 0.75rem;
            text-align: left;
            font: inherit;
            color: var(--text-secondary);
            background: transparent;
            border: 1px solid transparent;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;

            mat-icon {
              color: var(--accent-primary);
              font-size: 1.25rem;
              width: 1.25rem;
              height: 1.25rem;
              flex-shrink: 0;
            }

            .example-query-text {
              flex: 1;
            }

            &:hover,
            &:focus-visible {
              color: var(--text-primary);
              background: var(--bg-card);
              border-color: var(--border-color);
            }

            &:focus-visible {
              outline: 2px solid var(--accent-primary);
              outline-offset: 2px;
            }
          }
        }
      }
    }
    
    @keyframes glow-pulse {
      0%, 100% {
        opacity: 1;
        transform: scale(1);
      }
      50% {
        opacity: 0.7;
        transform: scale(1.05);
      }
    }

    .results-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border-color);
      transition: border-color 0.3s ease;
      
      h2 {
        color: var(--text-primary);
        font-size: 1.5rem;
        margin: 0;
      }
      
      .results-info {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 0.25rem;
        font-size: 0.9rem;
        color: var(--text-secondary);
        
        .filtered-info {
          color: var(--accent-warning);
        }
        
        .timing-info {
          font-family: 'Courier New', monospace;
          color: var(--accent-success);
        }
      }
    }

    .results-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
      gap: 1.5rem;
      animation: fadeIn 0.5s ease-out;
    }

    .pagination-container {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 0.75rem;
      margin-top: 1.5rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }

    .pagination-btn,
    .page-btn {
      color: var(--text-primary) !important;
      border-color: var(--border-color) !important;
      background: var(--bg-card) !important;
      transition: all 0.2s ease;

      mat-icon {
        font-size: 1.1rem;
        width: 1.1rem;
        height: 1.1rem;
      }

      &:hover {
        border-color: var(--border-hover) !important;
        background: var(--bg-card-hover) !important;
      }
    }

    .pagination-btn {
      min-width: 96px;
    }

    .page-numbers {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      justify-content: center;
    }

    .page-btn {
      min-width: 44px;
      padding: 0 0.5rem;

      &.active {
        border-color: var(--accent-primary) !important;
        color: var(--accent-primary) !important;
        background: var(--bg-secondary) !important;
      }
    }

    @keyframes fadeIn {
      from {
        opacity: 0;
        transform: translateY(20px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .no-results {
      text-align: center;
      padding: 4rem 2rem;
      
      .vast-logo-glow {
        height: 30px;
        width: auto;
        margin-bottom: 2rem;
        filter: brightness(0) invert(1) drop-shadow(0 0 15px rgba(0, 217, 255, 0.7));
        animation: glow-pulse 1.2s ease-in-out infinite;
        transition: filter 0.3s ease;
      }
      
      [data-theme="light"] & .vast-logo-glow {
        filter: none drop-shadow(0 0 15px rgba(0, 206, 209, 0.5));
      }
      
      h2 {
        color: var(--text-primary);
        font-size: 1.75rem;
        margin-bottom: 0.5rem;
      }
      
      p {
        color: var(--text-muted);
      }
    }
  `]
})
export class SearchPageComponent implements OnInit, OnDestroy, AfterViewInit {
  private static readonly PAGE_SIZE = 20;
  private static readonly SUGGESTIONS_POLL_MS = 300_000;

  @ViewChild(SearchBarComponent) private searchBar?: SearchBarComponent;

  searchService = inject(SearchService);
  dialog = inject(MatDialog);
  authService = inject(AuthService);
  suggestionsService = inject(SuggestionsService);
  modelsService = inject(BackendModelsService);
  private pageRefresh = inject(PageRefreshService);
  
  hasSearched = signal(false);
  exampleQueries = signal<string[]>([]);
  suggestionsLoading = signal(true);
  suggestionsAccessWarning = signal<string | null>(null);
  suggestionsUpdatedAt = signal<string | null>(null);
  private suggestionsSub?: Subscription;
  private pageRefreshSub?: Subscription;
  isOpeningDialog = signal(false);
  currentPage = signal(1);
  private uploadDialogRef: MatDialogRef<UploadDialogComponent> | null = null;

  ngOnInit() {
    void this.modelsService.ensureLoaded();
    this.restoreSearchSession();
    if (this.showSuggestions()) {
      this.loadExampleQueries();
    }
    this.suggestionsSub = interval(SearchPageComponent.SUGGESTIONS_POLL_MS).subscribe(
      () => {
        if (this.showSuggestions()) {
          this.loadExampleQueries(true);
        }
      },
    );
    this.pageRefreshSub = this.pageRefresh.refresh$.subscribe(() => this.resetSearchView());
  }

  ngAfterViewInit() {
    const query = this.searchService.state().query?.trim();
    if (query) {
      this.searchBar?.setQuery(query);
    }
  }

  ngOnDestroy() {
    this.suggestionsSub?.unsubscribe();
    this.pageRefreshSub?.unsubscribe();
  }

  loadExampleQueries(silent = false) {
    if (!silent) {
      this.suggestionsLoading.set(true);
    }
    this.suggestionsService.getSuggestions({ chipsOnly: true }).subscribe({
      next: (data) => {
        this.suggestionsLoading.set(false);
        if (data.prompts_table_available === false) {
          this.suggestionsAccessWarning.set(
            data.table_message?.trim() || friendlyVastDbAccessMessage('suggestions'),
          );
          if (!silent || !this.exampleQueries().length) {
            this.exampleQueries.set([]);
            this.suggestionsUpdatedAt.set(null);
          }
          return;
        }
        this.suggestionsAccessWarning.set(null);
        const prompts = (data.search_prompts ?? []).slice(0, 10);
        const genAt = data.generated_at ?? null;
        if (!prompts.length) {
          if (!silent || !this.exampleQueries().length) {
            this.exampleQueries.set([]);
            this.suggestionsUpdatedAt.set(null);
          }
          return;
        }
        if (
          silent &&
          genAt === this.suggestionsUpdatedAt() &&
          JSON.stringify(prompts) === JSON.stringify(this.exampleQueries())
        ) {
          return;
        }
        this.suggestionsUpdatedAt.set(genAt);
        this.exampleQueries.set(prompts);
      },
      error: (err) => {
        this.suggestionsLoading.set(false);
        this.suggestionsAccessWarning.set(resolveApiAccessWarning(err, 'suggestions'));
        if (!silent) {
          console.error('Failed to load search suggestions', err);
        }
        if (!silent || !this.exampleQueries().length) {
          this.exampleQueries.set([]);
        }
      },
    });
  }
  
  constructor() {
    effect(() => {
      const status = this.authService.status();
      if (status === 'pending' && !this.authService.token()) {
        if (this.uploadDialogRef) {
          this.uploadDialogRef.close();
          this.uploadDialogRef = null;
        }
        this.dialog.closeAll();
      }
    });

    // Keep page index valid when result count changes.
    effect(() => {
      const totalPages = this.totalPages();
      if (this.currentPage() > totalPages) {
        this.currentPage.set(totalPages);
      }
    });
  }

  onSearch(request: SearchRequest) {
    this.hasSearched.set(true);
    this.currentPage.set(1);
    this.searchService.search(request);
  }

  onExampleQueryClick(example: string) {
    this.searchBar?.setQuery(example.trim());
  }

  showSuggestions(): boolean {
    const state = this.searchService.state();
    return (
      !this.hasSearched() &&
      !state.loading &&
      this.displayResults().length === 0
    );
  }

  private restoreSearchSession() {
    const state = this.searchService.state();
    if (state.chunkResults.length > 0 || state.results.length > 0 || state.query?.trim()) {
      this.hasSearched.set(true);
    }
  }

  private resetSearchView() {
    this.searchService.clearResults();
    this.searchService.closeAnimation();
    this.hasSearched.set(false);
    this.currentPage.set(1);
    this.suggestionsAccessWarning.set(null);
    this.searchBar?.clearSearch();
    this.loadExampleQueries();
  }

  formatSuggestionsUpdated(iso: string | null): string {
    return formatAbsoluteTime(iso);
  }

  suggestionsUpdatedRelative(): string {
    return formatRelativeTime(this.suggestionsUpdatedAt());
  }

  displayResults(): ChunkSearchResult[] {
    const chunks = this.searchService.state().chunkResults;
    if (chunks.length > 0) return chunks;
    return [];
  }

  visibleChunks(): ChunkSearchResult[] {
    const start = (this.currentPage() - 1) * SearchPageComponent.PAGE_SIZE;
    return this.displayResults().slice(start, start + SearchPageComponent.PAGE_SIZE);
  }

  trackChunk(_index: number, chunk: ChunkSearchResult): string {
    return `${chunk.original_video}|${chunk.query}|${chunk.best_segment_number}`;
  }

  pageStartIndex(): number {
    const total = this.displayResults().length;
    if (total === 0) return 0;
    return (this.currentPage() - 1) * SearchPageComponent.PAGE_SIZE + 1;
  }

  pageEndIndex(): number {
    return Math.min(this.currentPage() * SearchPageComponent.PAGE_SIZE, this.displayResults().length);
  }

  totalPages(): number {
    const total = this.displayResults().length;
    return Math.max(1, Math.ceil(total / SearchPageComponent.PAGE_SIZE));
  }

  hasMultiplePages(): boolean {
    return this.totalPages() > 1;
  }

  pageNumbers(): number[] {
    return Array.from({ length: this.totalPages() }, (_, i) => i + 1);
  }

  canGoPrev(): boolean {
    return this.currentPage() > 1;
  }

  canGoNext(): boolean {
    return this.currentPage() < this.totalPages();
  }

  goToPage(page: number) {
    if (page < 1 || page > this.totalPages()) return;
    this.currentPage.set(page);
  }

  goPrevPage() {
    if (!this.canGoPrev()) return;
    this.currentPage.update(p => p - 1);
  }

  goNextPage() {
    if (!this.canGoNext()) return;
    this.currentPage.update(p => p + 1);
  }

  closeAnimation() {
    this.searchService.closeAnimation();
  }

  openChunk(chunk: ChunkSearchResult, seekSec?: number) {
    this.dialog.open(VideoPlayerComponent, {
      data: {
        chunk,
        query: chunk.query,
        initialSeekSec: seekSec ?? chunk.best_match_start_sec,
      },
      width: '92vw',
      maxWidth: '1100px',
      height: '92vh',
      panelClass: 'video-player-dialog',
    });
  }

  openChunkAt(chunk: ChunkSearchResult, seekSec: number) {
    this.openChunk(chunk, seekSec);
  }

  openUpload() {
    if (this.isOpeningDialog()) return;
    this.isOpeningDialog.set(true);
    setTimeout(() => {
      this.uploadDialogRef = this.dialog.open(UploadDialogComponent, {
        width: '600px',
        maxWidth: '95vw',
        maxHeight: '85vh',
        panelClass: 'upload-dialog',
        disableClose: false,
        autoFocus: true,
        restoreFocus: true
      });
      this.isOpeningDialog.set(false);
      this.uploadDialogRef.afterClosed().subscribe(() => {
        this.uploadDialogRef = null;
      });
    }, 0);
  }
}


