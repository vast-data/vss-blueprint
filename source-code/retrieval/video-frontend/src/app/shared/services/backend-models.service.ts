import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface BackendModelsConfig {
  embedding: {
    embedding_model: string;
    embedding_dimensions: number;
    embedding_local_nim: boolean;
  };
  synthesis: {
    cosmos_model: string;
    cosmos_host?: string;
  };
  display_timezone: string;
}

const DEFAULT_MODELS: BackendModelsConfig = {
  embedding: {
    embedding_model: 'nvidia/cosmos-embed1',
    embedding_dimensions: 256,
    embedding_local_nim: true,
  },
  synthesis: {
    cosmos_model: 'nvidia/cosmos3-nano-reasoner',
  },
  display_timezone: 'UTC',
};

@Injectable({ providedIn: 'root' })
export class BackendModelsService {
  private http = inject(HttpClient);
  private readonly models = signal<BackendModelsConfig>(DEFAULT_MODELS);
  private loadPromise: Promise<void> | null = null;

  readonly embeddingLabel = computed(
    () => this.models().embedding.embedding_model
  );
  readonly embeddingDetail = computed(() => {
    const e = this.models().embedding;
    const host = e.embedding_local_nim ? 'local NIM' : 'NVIDIA Cloud';
    return `${e.embedding_model} (${e.embedding_dimensions} dims, ${host})`;
  });
  readonly synthesisLabel = computed(() => this.models().synthesis.cosmos_model);
  readonly displayTimezone = computed(() => this.models().display_timezone);

  ensureLoaded(): Promise<void> {
    if (this.loadPromise) {
      return this.loadPromise;
    }
    this.loadPromise = this.loadFromApi()
      .catch(() => {
        this.models.set(DEFAULT_MODELS);
      })
      .finally(() => {
        this.loadPromise = null;
      });
    return this.loadPromise;
  }

  private async loadFromApi(): Promise<void> {
    const data = await firstValueFrom(
      this.http.get<{
        embedding?: Partial<BackendModelsConfig['embedding']>;
        synthesis?: Partial<BackendModelsConfig['synthesis']>;
        app?: { display_timezone?: string };
        llm?: { llm_model_name?: string };
      }>(`${environment.apiUrl}/config`)
    );
    this.models.set({
      embedding: {
        embedding_model: data.embedding?.embedding_model ?? DEFAULT_MODELS.embedding.embedding_model,
        embedding_dimensions:
          data.embedding?.embedding_dimensions ?? DEFAULT_MODELS.embedding.embedding_dimensions,
        embedding_local_nim:
          data.embedding?.embedding_local_nim ?? DEFAULT_MODELS.embedding.embedding_local_nim,
      },
      synthesis: {
        cosmos_model:
          data.synthesis?.cosmos_model
          ?? data.llm?.llm_model_name
          ?? DEFAULT_MODELS.synthesis.cosmos_model,
      },
      display_timezone:
        data.app?.display_timezone?.trim() || DEFAULT_MODELS.display_timezone,
    });
  }
}
