import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ChunkSearchResult } from '../../../shared/models/video.model';
import { ExploreService } from '../services/explore.service';
import { LLMSynthesisComponent } from '../../search/components/llm-synthesis.component';

export interface VideoSummarizeDialogData {
  chunk: ChunkSearchResult;
}

@Component({
  selector: 'app-video-summarize-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    LLMSynthesisComponent,
  ],
  template: `
    <div class="dialog-shell">
      <header class="dialog-header">
        <div class="title-block">
          <mat-icon class="title-icon">auto_awesome</mat-icon>
          <div>
            <h2>Summarize Video</h2>
            <p>{{ data.chunk.filename }}</p>
          </div>
        </div>
        <button mat-icon-button type="button" (click)="close()" aria-label="Close">
          <mat-icon>close</mat-icon>
        </button>
      </header>

      @if (loading()) {
        <div class="loading-state">
          <mat-spinner diameter="44"></mat-spinner>
          <p>Analyzing {{ data.chunk.total_segments }} segments…</p>
        </div>
      } @else if (error()) {
        <div class="error-state">
          <mat-icon>error_outline</mat-icon>
          <span>{{ error() }}</span>
        </div>
      } @else if (synthesis()) {
        <app-llm-synthesis [synthesis]="synthesis()!"></app-llm-synthesis>
      }

      <footer class="dialog-footer">
        <button mat-stroked-button type="button" (click)="close()">Close</button>
      </footer>
    </div>
  `,
  styles: [`
    .dialog-shell {
      padding: 1.25rem 1.5rem 1rem;
      max-height: 85vh;
      overflow: auto;
      background: var(--bg-primary);
    }

    .dialog-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .title-block {
      display: flex;
      gap: 0.75rem;
      align-items: flex-start;

      h2 {
        margin: 0;
        font-size: 1.25rem;
        color: var(--text-primary);
      }

      p {
        margin: 0.25rem 0 0;
        color: var(--text-secondary);
        font-family: var(--font-name);
        font-size: 0.72rem;
        font-weight: 400;
        letter-spacing: 0.01em;
      }
    }

    .title-icon {
      color: var(--accent-primary);
      font-size: 1.75rem;
      width: 1.75rem;
      height: 1.75rem;
    }

    .loading-state,
    .error-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 1rem;
      padding: 3rem 1rem;
      text-align: center;
      color: var(--text-secondary);
    }

    .error-state {
      color: #fca5a5;

      mat-icon {
        font-size: 2rem;
        width: 2rem;
        height: 2rem;
        color: #ef4444;
      }
    }

    .dialog-footer {
      display: flex;
      justify-content: flex-end;
      margin-top: 0.5rem;
      padding-top: 0.75rem;
      border-top: 1px solid var(--border-color);
    }

  `],
})
export class VideoSummarizeDialogComponent implements OnInit {
  private explore = inject(ExploreService);
  private dialogRef = inject(MatDialogRef<VideoSummarizeDialogComponent>);
  data = inject<VideoSummarizeDialogData>(MAT_DIALOG_DATA);

  loading = signal(true);
  error = signal<string | null>(null);
  synthesis = signal<import('../../../shared/models/video.model').LLMSynthesis | null>(null);

  ngOnInit() {
    this.explore
      .synthesize({
        original_video: this.data.chunk.original_video,
      })
      .subscribe({
        next: (res) => {
          this.loading.set(false);
          this.synthesis.set(res.llm_synthesis);
        },
        error: (err) => {
          this.loading.set(false);
          this.error.set(err?.error?.detail || 'Failed to summarize video');
        },
      });
  }

  close() {
    this.dialogRef.close();
  }
}
