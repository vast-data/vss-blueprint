import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { 
  SearchRequest, 
  SearchResponse, 
  UploadRequest, 
  UploadResponse,
  VideoSearchResult 
} from '../models/video.model';

@Injectable({
  providedIn: 'root'
})
export class VideoService {
  private http = inject(HttpClient);
  private apiUrl = environment.apiUrl;

  /**
   * Perform semantic video search
   */
  search(request: SearchRequest): Observable<SearchResponse> {
    return this.http.post<SearchResponse>(`${this.apiUrl}/search`, request);
  }

  /**
   * URL for the HTML5 video element: backend proxies S3 bytes (no re-encode; same quality as the object).
   * Supports HTTP Range for seeking. Token must be in the query (video tags cannot set headers).
   *
   * Optional: GET /videos/playback-url returns a presigned S3 URL (identical bytes); requires
   * bucket CORS for browser playback if you switch to that.
   */
  getStreamUrl(source: string, token: string): string {
    const params = new URLSearchParams({ source, token });
    return `${this.apiUrl}/videos/stream?${params.toString()}`;
  }

  /**
   * Same object as the stream proxy, as a time-limited presigned S3 GET URL.
   * Configure S3 bucket CORS before using this as the video element src from the browser.
   */
  getPlaybackPresignedUrl(source: string, token: string, expiresIn = 3600) {
    const params = new URLSearchParams({
      source,
      token,
      expires_in: String(expiresIn),
    });
    return this.http.get<{ url: string; expires_in: number }>(
      `${this.apiUrl}/videos/playback-url?${params.toString()}`
    );
  }

  /**
   * Get metadata for a specific video segment
   */
  getVideoMetadata(source: string): Observable<any> {
    return this.http.get(`${this.apiUrl}/videos/metadata`, {
      params: { source }
    });
  }

  /**
   * Upload video file directly to backend (backend proxies to S3)
   * @param metadata Optional metadata: camera_id, capture_type, location, custom_prompt
   */
  uploadVideo(
    file: File, 
    is_public: boolean, 
    tags: string[], 
    allowed_users: string[], 
    scenario: string = '',
    metadata?: { camera_id?: string; capture_type?: string; location?: string; custom_prompt?: string }
  ): Observable<any> {
    console.log('uploadVideo called:', { 
      fileName: file.name, 
      size: file.size, 
      is_public, 
      tags, 
      allowed_users,
      scenario,
      metadata
    });
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('is_public', is_public.toString());
    
    if (tags && tags.length > 0) {
      formData.append('tags', tags.join(','));
    }
    
    if (allowed_users && allowed_users.length > 0) {
      formData.append('allowed_users', allowed_users.join(','));
    }
    
    if (scenario) {
      formData.append('scenario', scenario);
    }
    
    if (metadata?.camera_id) {
      formData.append('camera_id', metadata.camera_id);
    }
    if (metadata?.capture_type) {
      formData.append('capture_type', metadata.capture_type);
    }
    if (metadata?.location) {
      formData.append('location', metadata.location);
    }
    if (metadata?.custom_prompt) {
      formData.append('custom_prompt', metadata.custom_prompt);
    }
    
    console.log('Uploading to backend...');
    return this.http.post(`${this.apiUrl}/videos/upload`, formData);
  }
}

