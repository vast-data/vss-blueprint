import { ChangeDetectionStrategy, Component, inject, signal, OnInit } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../services/auth.service';
import { NgOptimizedImage } from '@angular/common';
import { MatFormField, MatLabel, MatSuffix } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinner } from '@angular/material/progress-spinner';
import { MatButton, MatIconButton } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { Router, ActivatedRoute } from '@angular/router';
import { SETTINGS } from '../../../../shared/settings';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    FormsModule,
    NgOptimizedImage,
    MatFormField,
    MatLabel,
    MatSuffix,
    MatInputModule,
    MatProgressSpinner,
    MatButton,
    MatIconButton,
    MatIcon
  ],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class LoginComponent implements OnInit {
  fb = inject(FormBuilder);
  authService = inject(AuthService);
  router = inject(Router);
  route = inject(ActivatedRoute);

  form = this.fb.group({
    username: [localStorage.getItem('cached_username') || '', Validators.required],
    secretKey: ['', Validators.required]
  });
  
  // Secret key visibility toggle
  hideSecretKey = signal(true);

  ngOnInit() {
    // Handle auto-login from blueprints page
    // Try multiple methods to get the token since Angular hash routing can be tricky
    
    console.log('[AUTO-LOGIN] === INIT ===');
    console.log('[AUTO-LOGIN] Full URL:', window.location.href);
    console.log('[AUTO-LOGIN] Hash:', window.location.hash);
    console.log('[AUTO-LOGIN] Search:', window.location.search);
    
    // Method 1: Check Angular's query params
    const queryParams = this.route.snapshot.queryParams;
    console.log('[AUTO-LOGIN] Angular queryParams:', JSON.stringify(queryParams));
    
    // Method 2: Parse from hash manually (fallback)
    let token = queryParams['token'];
    let username = queryParams['username'];
    
    if (!token && window.location.href.includes('token=')) {
      console.log('[AUTO-LOGIN] Token not in queryParams, parsing from URL...');
      // Try to extract from the full URL
      const urlMatch = window.location.href.match(/[?&]token=([^&]+)/);
      if (urlMatch) {
        token = decodeURIComponent(urlMatch[1]);
        console.log('[AUTO-LOGIN] Extracted token from URL');
      }
      const userMatch = window.location.href.match(/[?&]username=([^&]+)/);
      if (userMatch) {
        username = decodeURIComponent(userMatch[1]);
      }
    }
    
    console.log('[AUTO-LOGIN] Token found:', token ? 'YES (length: ' + token.length + ')' : 'NO');
    console.log('[AUTO-LOGIN] Username:', username || 'NOT SET');
    
    if (token) {
      console.log('[AUTO-LOGIN] Setting auth state via AuthService...');
      
      // Update AuthService signal state (not just localStorage!)
      // This is required because route guards check the signal, not localStorage
      this.authService.setAutoLoginState(token, username || 'user');
      
      // Cache username for future logins
      if (username) {
        localStorage.setItem('cached_username', username);
      }
      
      // Navigate to default URL
      console.log('[AUTO-LOGIN] Navigating to:', SETTINGS.DEFAULT_URL);
      this.router.navigate([SETTINGS.DEFAULT_URL]);
    }
  }

  onLogin() {
    if (this.form.valid) {
      const { username, secretKey } = this.form.value;
      if (username && secretKey) {
        localStorage.setItem('cached_username', username);
        this.authService.login(username, secretKey);
      }
    }
  }
}

