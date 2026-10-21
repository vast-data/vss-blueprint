import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { IngestMetadataConfig } from '../models/ingest-metadata.model';
import {
  DEFAULT_INGEST_METADATA_CONFIG,
  isValidIngestMetadataConfig,
} from '../utils/ingest-metadata.defaults';

@Injectable({ providedIn: 'root' })
export class IngestMetadataService {
  private http = inject(HttpClient);
  readonly config = signal<IngestMetadataConfig | null>(null);
  readonly loading = signal(false);
  readonly loadError = signal<string | null>(null);
  /** Bundled metadata config. */
  readonly usingFallback = signal(false);

  private loadPromise: Promise<void> | null = null;

  ensureLoaded(): Promise<void> {
    if (this.config()) {
      return Promise.resolve();
    }
    if (this.loadPromise) {
      return this.loadPromise;
    }

    this.loading.set(true);
    this.loadError.set(null);
    this.loadPromise = this.loadFromApi()
      .catch((err) => this.applyFallback(err))
      .finally(() => {
        this.loading.set(false);
        this.loadPromise = null;
      });

    return this.loadPromise;
  }

  private async loadFromApi(): Promise<void> {
    const data = await firstValueFrom(
      this.http.get<IngestMetadataConfig>(`${environment.apiUrl}/metadata/ingest-config`)
    );
    if (!isValidIngestMetadataConfig(data)) {
      throw new Error('Invalid ingest metadata response shape');
    }
    this.config.set(data);
    this.usingFallback.set(false);
    this.loadError.set(null);
  }

  private applyFallback(err: unknown): void {
    const detail =
      err instanceof HttpErrorResponse
        ? `HTTP ${err.status}${err.statusText ? ` ${err.statusText}` : ''}`
        : err instanceof Error
          ? err.message
          : 'unknown error';
    console.warn('[IngestMetadata] API unavailable, using bundled defaults:', detail, err);
    this.config.set(DEFAULT_INGEST_METADATA_CONFIG);
    this.usingFallback.set(true);
    this.loadError.set(null);
  }
}
