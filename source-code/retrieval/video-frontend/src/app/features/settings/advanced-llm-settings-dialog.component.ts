import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSliderModule } from '@angular/material/slider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';

// Storage keys for localStorage
export const LLM_SETTINGS_STORAGE_KEY = 'video_lab_llm_settings';

/** Max clip cards (backend VideoSearchRequest.top_k le=100). */
export const MAX_SEARCH_RESULTS = 100;

/** Max clips sent to LLM synthesis (backend llm_top_n le=100). */
export const MAX_LLM_ANALYSIS_COUNT = 100;

export const LLM_CLIP_COUNT_OPTIONS = [1, 2, 3, 5, 10, 15, 20, 30, 50, 100] as const;

export const CLIP_CARD_COUNT_OPTIONS = [3, 5, 10, 15, 20, 30, 50, 100] as const;

export interface LLMSettings {
  /** Top grouped clip cards shown in results (top_k). */
  searchTopK: number;
  /** Top clip cards sent to LLM with full timeline reasoning text + object_classes. */
  llmTopNSummaries: number;
  /** Hybrid search: caption text weight (remainder = video embedding). */
  hybridTextWeight: number;
  minSimilarityScore: number;
}

export const DEFAULT_LLM_SETTINGS: LLMSettings = {
  searchTopK: 15,
  llmTopNSummaries: 3,
  hybridTextWeight: 0.6,
  minSimilarityScore: 0.1,
};

function normalizeOption(n: unknown, fallback: number, options: readonly number[], max: number): number {
  let raw: number;
  if (typeof n === 'number' && !Number.isNaN(n)) {
    raw = n;
  } else if (typeof n === 'string' && n.trim() !== '' && !Number.isNaN(Number(n))) {
    raw = Number(n);
  } else {
    raw = fallback;
  }
  const clamped = Math.min(Math.max(1, raw), max);
  if (options.includes(clamped)) {
    return clamped;
  }
  const nextUp = options.find((o) => o >= clamped);
  return nextUp ?? options[options.length - 1];
}

function normalizeLlmTopNSummaries(n: unknown): number {
  return normalizeOption(n, DEFAULT_LLM_SETTINGS.llmTopNSummaries, LLM_CLIP_COUNT_OPTIONS, MAX_LLM_ANALYSIS_COUNT);
}

function normalizeSearchTopK(n: unknown): number {
  return normalizeOption(n, DEFAULT_LLM_SETTINGS.searchTopK, CLIP_CARD_COUNT_OPTIONS, MAX_SEARCH_RESULTS);
}

function normalizeHybridTextWeight(n: unknown): number {
  let raw = DEFAULT_LLM_SETTINGS.hybridTextWeight;
  if (typeof n === 'number' && !Number.isNaN(n)) {
    raw = n;
  } else if (typeof n === 'string' && n.trim() !== '' && !Number.isNaN(Number(n))) {
    raw = Number(n);
  }
  return Math.min(1, Math.max(0, Math.round(raw * 100) / 100));
}

// Helper function to get settings (can be used by other components)
export function getLLMSettings(): LLMSettings {
  const stored = localStorage.getItem(LLM_SETTINGS_STORAGE_KEY);
  if (stored) {
    try {
      const merged = { ...DEFAULT_LLM_SETTINGS, ...JSON.parse(stored) } as LLMSettings;
      merged.searchTopK = normalizeSearchTopK(merged.searchTopK);
      merged.llmTopNSummaries = normalizeLlmTopNSummaries(merged.llmTopNSummaries);
      merged.hybridTextWeight = normalizeHybridTextWeight(merged.hybridTextWeight);
      if (merged.llmTopNSummaries > merged.searchTopK) {
        merged.llmTopNSummaries = merged.searchTopK;
      }
      return merged;
    } catch {
      return DEFAULT_LLM_SETTINGS;
    }
  }
  return DEFAULT_LLM_SETTINGS;
}

@Component({
  selector: 'app-advanced-llm-settings-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatSelectModule,
    MatSliderModule,
    MatTooltipModule,
    MatSnackBarModule
  ],
  template: `
    <div class="dialog-container">
      <div class="dialog-header">
        <div class="header-title">
          <mat-icon>tune</mat-icon>
          <h2>Advanced Search &amp; AI Settings</h2>
        </div>
        <button mat-icon-button class="close-btn" (click)="close()">
          <mat-icon>close</mat-icon>
        </button>
      </div>

      <div class="dialog-content">
        <p class="description">
          Tune hybrid clip search and AI synthesis. Search always blends caption + video embeddings;
          AI summary runs automatically on every search using timeline reasoning text and object_classes from each clip.
        </p>

        <!-- Max clip cards -->
        <div class="setting-row">
          <div class="setting-label">
            <span class="label-text">Max Clip Cards</span>
            <button mat-icon-button class="info-btn"
                    matTooltip="Maximum upload clips shown as grouped cards (one card per video). Backend fetches extra segment hits internally to build each timeline."
                    matTooltipPosition="right">
              <mat-icon>info_outline</mat-icon>
            </button>
          </div>
          <mat-form-field appearance="outline" class="setting-field">
            <mat-select [(ngModel)]="settings.searchTopK">
              @for (n of clipCardCountOptions; track n) {
                <mat-option [value]="n">{{ clipLabel(n) }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
        </div>

        <!-- LLM clips analyzed -->
        <div class="setting-row">
          <div class="setting-label">
            <span class="label-text">LLM Clips Analyzed</span>
            <button mat-icon-button class="info-btn"
                    matTooltip="Number of top clip cards sent to the LLM, each with full segment timeline (reasoning text, object_classes). More clips = richer answers but higher latency and token cost."
                    matTooltipPosition="right">
              <mat-icon>info_outline</mat-icon>
            </button>
          </div>
          <mat-form-field appearance="outline" class="setting-field">
            <mat-select [(ngModel)]="settings.llmTopNSummaries">
              @for (n of llmClipCountOptions; track n) {
                <mat-option [value]="n" [disabled]="n > settings.searchTopK">{{ clipLabel(n) }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
        </div>

        <!-- Hybrid text vs video weight -->
        <div class="setting-row slider-row">
          <div class="setting-label">
            <span class="label-text">Caption vs Video Weight</span>
            <button mat-icon-button class="info-btn"
                    matTooltip="Hybrid search blend: left = caption/text embeddings, right = segment video embeddings. Default 60% caption / 40% video."
                    matTooltipPosition="right">
              <mat-icon>info_outline</mat-icon>
            </button>
          </div>
          <div class="slider-container">
            <div class="slider-labels">
              <span class="slider-label">Video ({{ videoWeightLabel }})</span>
              <span class="slider-value">{{ settings.hybridTextWeight.toFixed(2) }} caption</span>
              <span class="slider-label">Caption (1.0)</span>
            </div>
            <mat-slider min="0" max="1" step="0.05" class="similarity-slider">
              <input matSliderThumb [(ngModel)]="settings.hybridTextWeight">
            </mat-slider>
          </div>
        </div>

        <!-- Minimum Similarity Score -->
        <div class="setting-row slider-row">
          <div class="setting-label">
            <span class="label-text">Minimum Similarity</span>
            <button mat-icon-button class="info-btn"
                    matTooltip="Minimum hybrid score for a clip card to appear. Lower = more clips (may include weaker matches); higher = stricter. Recommended: 0.4–0.6"
                    matTooltipPosition="right">
              <mat-icon>info_outline</mat-icon>
            </button>
          </div>
          <div class="slider-container">
            <div class="slider-labels">
              <span class="slider-label">Broad (0.1)</span>
              <span class="slider-value">{{ settings.minSimilarityScore.toFixed(2) }}</span>
              <span class="slider-label">Strict (0.8)</span>
            </div>
            <mat-slider min="0.1" max="0.8" step="0.05" class="similarity-slider">
              <input matSliderThumb [(ngModel)]="settings.minSimilarityScore">
            </mat-slider>
          </div>
        </div>

        <!-- Info Box -->
        <div class="info-box">
          <mat-icon>lightbulb</mat-icon>
          <div class="info-content">
            <strong>Tips:</strong>
            <ul>
              <li>Object/brand queries: try higher caption weight; visual appearance queries: lower caption weight</li>
              <li>LLM clips analyzed must be ≤ max clip cards shown</li>
              <li>Lower similarity (0.2–0.4) for exploration; higher (0.5–0.7) for precision</li>
              <li>Custom system prompt (menu) shapes AI answer style; evidence comes from reasoning text and object_classes</li>
            </ul>
          </div>
        </div>
      </div>

      <div class="dialog-actions">
        <button mat-button class="reset-btn" (click)="resetToDefaults()">
          <mat-icon>refresh</mat-icon>
          Reset Defaults
        </button>
        <div class="action-buttons">
          <button mat-button class="cancel-btn" (click)="close()">Cancel</button>
          <button mat-raised-button class="save-btn" (click)="save()">
            <mat-icon>save</mat-icon>
            Save Settings
          </button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    :host {
      display: flex;
      flex-direction: column;
      max-height: 85vh;
      min-height: 0;
      overflow: hidden;
    }

    .dialog-container {
      background: var(--bg-card);
      color: var(--text-primary);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      min-width: 500px;
      max-height: 85vh;
      min-height: 0;
      flex: 1 1 auto;
      transition: background 0.3s ease, color 0.3s ease;
    }

    .dialog-header {
      flex: 0 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1rem 1.5rem;
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border-color);
      transition: background 0.3s ease, border-color 0.3s ease;

      .header-title {
        display: flex;
        align-items: center;
        gap: 0.75rem;

        h2 {
          margin: 0;
          font-size: 1.2rem;
          font-weight: 600;
          color: var(--accent-primary);
        }

        mat-icon {
          font-size: 1.8rem;
          width: 1.8rem;
          height: 1.8rem;
          color: var(--accent-primary);
        }
      }

      .close-btn {
        color: var(--text-secondary);
        &:hover {
          color: var(--text-primary);
          background: var(--bg-card-hover);
        }
      }
    }

    .dialog-content {
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
      flex: 1 1 auto;
      min-height: 0;
      overflow-y: auto;
      max-height: calc(85vh - 9.5rem);
    }

    .description {
      font-size: 0.9rem;
      color: var(--text-secondary);
      line-height: 1.5;
      margin: 0;
    }

    .setting-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      
      &.slider-row {
        flex-direction: column;
        align-items: stretch;
      }
    }

    .setting-label {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      flex-shrink: 0;

      .label-text {
        font-size: 0.95rem;
        font-weight: 500;
        color: var(--text-primary);
      }

      .info-btn {
        width: 24px;
        height: 24px;
        line-height: 24px;
        
        mat-icon {
          font-size: 16px;
          width: 16px;
          height: 16px;
          color: rgba(0, 206, 209, 0.7);
        }
        
        &:hover mat-icon {
          color: #00CED1;
        }
      }
    }

    .setting-field {
      width: 160px;
      
      ::ng-deep {
        .mat-mdc-form-field-flex {
          background: var(--bg-secondary) !important;
        }

        .mat-mdc-text-field-wrapper {
          background: var(--bg-secondary) !important;
        }

        .mdc-notched-outline__leading,
        .mdc-notched-outline__notch,
        .mdc-notched-outline__trailing {
          border-color: rgba(255, 255, 255, 0.2) !important;
        }

        .mat-mdc-select-value {
          color: var(--text-primary) !important;
        }

        .mat-mdc-select-arrow {
          color: var(--text-secondary) !important;
        }
      }
    }

    .slider-container {
      width: 100%;
      padding: 0 0.5rem;
    }

    .slider-labels {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.25rem;
      
      .slider-label {
        font-size: 0.75rem;
        color: var(--text-muted);
      }
      
      .slider-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--accent-primary);
        background: var(--bg-secondary);
        padding: 0.25rem 0.75rem;
        border-radius: 4px;
      }
    }

    .similarity-slider {
      width: 100%;
      
      ::ng-deep {
        .mdc-slider__track--inactive {
          background: rgba(255, 255, 255, 0.2) !important;
        }
        
        .mdc-slider__track--active_fill {
          background: linear-gradient(90deg, #00CED1, #0047AB) !important;
          border-color: transparent !important;
        }
        
        .mdc-slider__thumb-knob {
          background: #00CED1 !important;
          border-color: #00CED1 !important;
          box-shadow: 0 0 10px rgba(0, 206, 209, 0.5);
          cursor: pointer !important;
        }
        
        .mdc-slider__thumb {
          cursor: pointer !important;
        }
      }
    }

    .info-box {
      display: flex;
      align-items: flex-start;
      gap: 1rem;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 1rem;
      font-size: 0.85rem;
      color: var(--text-primary);
      transition: background 0.3s ease, border-color 0.3s ease;

      mat-icon {
        color: var(--accent-warning);
        font-size: 1.5rem;
        width: 1.5rem;
        height: 1.5rem;
        flex-shrink: 0;
      }

      ul {
        margin: 0.5rem 0 0 0;
        padding-left: 1.2rem;
      }

      li {
        margin-bottom: 0.3rem;
        &:last-child {
          margin-bottom: 0;
        }
      }
    }

    .dialog-actions {
      flex: 0 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1rem 1.5rem;
      border-top: 1px solid var(--border-color);
      background: var(--bg-secondary);
      transition: background 0.3s ease, border-color 0.3s ease;

      .reset-btn {
        color: var(--text-muted);
        mat-icon {
          margin-right: 0.25rem;
        }
        &:hover {
          color: var(--text-primary);
          background: var(--bg-card-hover);
        }
      }

      .action-buttons {
        display: flex;
        gap: 1rem;
      }

      .cancel-btn {
        color: var(--text-secondary);
        &:hover {
          background: var(--bg-card-hover);
        }
      }

      .save-btn {
        background: var(--button-bg-primary) !important;
        color: var(--button-text) !important;
        
        * {
          color: var(--button-text) !important;
        }
        
        mat-icon {
          margin-right: 0.5rem;
        }
        &:hover {
          background: var(--button-bg-hover) !important;
          box-shadow: var(--shadow-hover);
        }
      }
    }
  `]
})
export class AdvancedLLMSettingsDialogComponent implements OnInit {
  private dialogRef = inject(MatDialogRef<AdvancedLLMSettingsDialogComponent>);
  private snackBar = inject(MatSnackBar);

  maxSearchResults = MAX_SEARCH_RESULTS;
  maxLlmAnalysisCount = MAX_LLM_ANALYSIS_COUNT;
  llmClipCountOptions = [...LLM_CLIP_COUNT_OPTIONS];
  clipCardCountOptions = [...CLIP_CARD_COUNT_OPTIONS];

  settings: LLMSettings = { ...DEFAULT_LLM_SETTINGS };

  get videoWeightLabel(): string {
    return (1 - this.settings.hybridTextWeight).toFixed(2);
  }

  clipLabel(n: number): string {
    return `${n} clip${n === 1 ? '' : 's'}`;
  }

  ngOnInit() {
    this.loadSettings();
  }

  loadSettings() {
    this.settings = getLLMSettings();
  }

  resetToDefaults() {
    this.settings = { ...DEFAULT_LLM_SETTINGS };
    this.snackBar.open('Settings reset to defaults', 'OK', {
      duration: 2000,
      panelClass: 'info-snackbar'
    });
  }

  save() {
    this.settings.searchTopK = normalizeSearchTopK(this.settings.searchTopK);
    this.settings.llmTopNSummaries = normalizeLlmTopNSummaries(this.settings.llmTopNSummaries);
    this.settings.hybridTextWeight = normalizeHybridTextWeight(this.settings.hybridTextWeight);

    if (this.settings.llmTopNSummaries > this.settings.searchTopK) {
      this.snackBar.open('LLM clips analyzed cannot exceed max clip cards. Adjusting…', 'OK', {
        duration: 3000,
        panelClass: 'warning-snackbar'
      });
      this.settings.llmTopNSummaries = this.settings.searchTopK;
    }

    localStorage.setItem(LLM_SETTINGS_STORAGE_KEY, JSON.stringify(this.settings));
    this.snackBar.open('Settings saved!', 'OK', {
      duration: 2000,
      panelClass: 'success-snackbar'
    });
    this.dialogRef.close(true);
  }

  close() {
    this.dialogRef.close(false);
  }
}

