import { computed, effect, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { SETTINGS } from '../../../shared/settings';
import { Router } from '@angular/router';
import { LoginRequest, LoginResponse, LoginState, UserInfo } from '../../../shared/models/auth.model';
import { environment } from '../../../../environments/environment';

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  httpClient = inject(HttpClient);
  router = inject(Router);

  state = signal<LoginState>({
    status: 'pending',
    error: null,
    token: localStorage.getItem(SETTINGS.LS_KEY_TOKEN),
    user: localStorage.getItem(SETTINGS.USER_KEY_TOKEN),
  });

  status = computed(() => this.state().status);
  token = computed(() => this.state().token);
  user = computed(() => this.state().user);
  error = computed(() => this.state().error);

  constructor() {
    // Sync token to localStorage
    effect(() => {
      const tokenValue = this.token();
      if (tokenValue) {
        localStorage.setItem(SETTINGS.LS_KEY_TOKEN, tokenValue);
      } else {
        localStorage.removeItem(SETTINGS.LS_KEY_TOKEN);
      }
    });

    // Sync user to localStorage
    effect(() => {
      const userValue = this.user();
      if (userValue) {
        localStorage.setItem(SETTINGS.USER_KEY_TOKEN, userValue);
      } else {
        localStorage.removeItem(SETTINGS.USER_KEY_TOKEN);
      }
    });
  }

  /**
   * Set auth state directly for auto-login (from blueprints page)
   * This updates the signal state so route guards recognize the authenticated user
   */
  setAutoLoginState(token: string, username: string) {
    console.log('[AuthService] Setting auto-login state for user:', username);
    this.state.update((state) => ({
      ...state,
      token: token,
      user: username,
      status: 'success',
      error: null
    }));
    console.log('[AuthService] Auto-login state set successfully');
  }

  login(username: string, password: string) {
    this.state.update((state) => ({ ...state, status: 'loading', error: null }));

    const loginData: LoginRequest = {
      username,
      password
    };

    this.httpClient.post<LoginResponse>(`${environment.apiUrl}/auth/login`, loginData)
      .subscribe({
        next: (response) => {
          // Login succeeded - store token and navigate
          this.state.update((state) => ({ 
            ...state, 
            token: response.access_token, 
            status: 'success',
            user: response.username,
            error: null
          }));
          this.router.navigate([SETTINGS.DEFAULT_URL]);
        },
        error: (response) => {
          const errorDetail = response.error?.detail || 'Authentication failed. Please check your username and password.';
          this.state.update((state) => ({ 
            ...state, 
            token: null, 
            status: 'error', 
            error: errorDetail
          }));
        }
      });
  }

  logout(useFullReload: boolean = true) {
    // Clear auth state
    this.state.update((state) => ({ ...state, status: 'pending', token: null, user: null, error: null }));
    
    // Clear localStorage
    localStorage.removeItem(SETTINGS.LS_KEY_TOKEN);
    localStorage.removeItem(SETTINGS.USER_KEY_TOKEN);
    
    if (useFullReload) {
      // Full page reload to clear all application state (search results, cached data, etc.)
      // This ensures a clean slate for the next user
      window.location.href = '/login';
    }
    // If useFullReload is false, the caller will handle navigation (e.g., interceptor)
  }
}

