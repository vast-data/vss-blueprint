import { Component, ViewChild, inject, signal, OnInit, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { SearchBarComponent } from './components/search-bar.component';
import { VideoCardComponent } from './components/video-card.component';
import { SearchAnimationComponent } from './components/search-animation.component';
import { LLMSynthesisComponent } from './components/llm-synthesis.component';
import { SearchService } from './services/search.service';
import { SearchRequest, VideoSearchResult } from '../../shared/models/video.model';
import { VideoPlayerComponent } from '../player/video-player.component';
import { UploadDialogComponent } from '../upload/upload-dialog.component';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { AuthService } from '../auth/services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-search-page',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    SearchBarComponent,
    VideoCardComponent,
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

      @if (!hasSearched() && !searchService.state().loading) {
        <div class="empty-state">
          <img src="assets/vast_logo.svg" alt="VAST" class="vast-logo-glow">
          <h2>What are you looking for?</h2>
          <p>Type what you want to see in plain words to find the right clips</p>
          <div class="examples">
            <h3>Try something like:</h3>
            <ul class="example-list">
              @for (example of exampleQueries(); track $index) {
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
          </div>
        </div>
      }

      @if (searchService.state().results.length > 0) {
        <div class="results-header">
          <h2>Search Results</h2>
          <div class="results-info">
            <span>Found {{ searchService.state().results.length }} videos</span>
            <span>
              Showing {{ pageStartIndex() }}-{{ pageEndIndex() }} / {{ searchService.state().results.length }}
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
          @for (video of visibleResults(); track video.source) {
            <app-video-card [video]="video" (play)="playVideo($event)"></app-video-card>
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

      @if (hasSearched() && searchService.state().results.length === 0 && !searchService.state().loading) {
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
      [resultsCount]="searchService.state().results.length"
      [showLlmPhase]="searchService.state().llmSynthesis !== null"
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
          margin-bottom: 0.75rem;
          font-size: 1rem;
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
export class SearchPageComponent implements OnInit {
  private static readonly PAGE_SIZE = 20;

  @ViewChild(SearchBarComponent) private searchBar?: SearchBarComponent;

  searchService = inject(SearchService);
  dialog = inject(MatDialog);
  authService = inject(AuthService);
  http = inject(HttpClient);
  
  hasSearched = signal(false);
  exampleQueries = signal<string[]>([]);
  isOpeningDialog = signal(false);
  currentPage = signal(1);
  private uploadDialogRef: MatDialogRef<UploadDialogComponent> | null = null;

  ngOnInit() {
    this.loadExampleQueries();
  }

  loadExampleQueries() {
    this.http.get<any>(`${environment.apiUrl}/frontend/search-suggestions`).subscribe({
      next: (config) => {
        if (config.placeholder_examples && config.placeholder_examples.length > 0) {
          this.exampleQueries.set(config.placeholder_examples);
        }
      },
      error: (err) => {
        console.error('Failed to load example queries, using defaults', err);
        // Set default fallback examples (friendly & varied)
        this.exampleQueries.set([
          'person waving at the camera',
          'someone dropping a bag',
          'car stopping at a red light'
        ]);
      }
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

  visibleResults(): VideoSearchResult[] {
    const start = (this.currentPage() - 1) * SearchPageComponent.PAGE_SIZE;
    const end = start + SearchPageComponent.PAGE_SIZE;
    return this.searchService.state().results.slice(start, end);
  }

  pageStartIndex(): number {
    const total = this.searchService.state().results.length;
    if (total === 0) return 0;
    return (this.currentPage() - 1) * SearchPageComponent.PAGE_SIZE + 1;
  }

  pageEndIndex(): number {
    return Math.min(this.currentPage() * SearchPageComponent.PAGE_SIZE, this.searchService.state().results.length);
  }

  totalPages(): number {
    const total = this.searchService.state().results.length;
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

  playVideo(video: VideoSearchResult) {
    this.dialog.open(VideoPlayerComponent, {
      data: { video },
      width: '90vw',
      maxWidth: '1200px',
      height: '90vh',
      panelClass: 'video-player-dialog'
    });
  }

  openUpload() {
    if (this.isOpeningDialog()) return;
    this.isOpeningDialog.set(true);
    setTimeout(() => {
      this.uploadDialogRef = this.dialog.open(UploadDialogComponent, {
        width: '600px',
        maxWidth: '95vw',
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


