import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../../environments/environment';
import {
  ChunkSearchResult,
  ExploreResponse,
  VideoDeleteResponse,
  VideoScope,
  VideoSynthesizeRequest,
  VideoSynthesizeResponse,
} from '../../../shared/models/video.model';

@Injectable({ providedIn: 'root' })
export class ExploreService {
  private http = inject(HttpClient);

  explore(params: {
    scope: VideoScope;
    date?: string | null;
    location?: string | null;
    cameraId?: string | null;
    indexed?: 'complete' | 'partial';
    limit?: number;
    offset?: number;
  }): Observable<ExploreResponse> {
    let httpParams = new HttpParams()
      .set('scope', params.scope)
      .set('indexed', params.indexed ?? 'complete')
      .set('limit', String(params.limit ?? 48))
      .set('offset', String(params.offset ?? 0));
    if (params.date) {
      httpParams = httpParams.set('date', params.date);
    }
    if (params.location) {
      httpParams = httpParams.set('location', params.location);
    }
    if (params.cameraId) {
      httpParams = httpParams.set('camera_id', params.cameraId);
    }
    return this.http.get<ExploreResponse>(`${environment.apiUrl}/videos/explore`, {
      params: httpParams,
    });
  }

  synthesize(body: VideoSynthesizeRequest): Observable<VideoSynthesizeResponse> {
    return this.http.post<VideoSynthesizeResponse>(
      `${environment.apiUrl}/videos/synthesize`,
      body
    );
  }

  getStreamChunk(
    streamId: string,
    chunkIndex: number,
    scope: VideoScope = 'all',
  ): Observable<ChunkSearchResult> {
    const params = new HttpParams()
      .set('stream_id', streamId)
      .set('chunk_index', String(chunkIndex))
      .set('scope', scope);
    return this.http.get<ChunkSearchResult>(`${environment.apiUrl}/videos/chunk`, { params });
  }

  deleteVideo(originalVideo: string): Observable<VideoDeleteResponse> {
    const params = new HttpParams().set('original_video', originalVideo);
    return this.http.delete<VideoDeleteResponse>(`${environment.apiUrl}/videos`, { params });
  }
}
