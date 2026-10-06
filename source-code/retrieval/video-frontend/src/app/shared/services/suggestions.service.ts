import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { SuggestionsResponse } from '../models/suggestions.model';

@Injectable({ providedIn: 'root' })
export class SuggestionsService {
  private http = inject(HttpClient);
  private apiUrl = environment.apiUrl;

  getSuggestions(options?: { chipsOnly?: boolean }): Observable<SuggestionsResponse> {
    const params = options?.chipsOnly ? { chips_only: 'true' } : undefined;
    return this.http.get<SuggestionsResponse>(`${this.apiUrl}/suggestions`, { params });
  }
}
