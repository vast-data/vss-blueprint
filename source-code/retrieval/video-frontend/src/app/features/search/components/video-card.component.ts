import { Component, EventEmitter, Input, Output, ViewChild, ElementRef, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { VideoSearchResult } from '../../../shared/models/video.model';
import { VideoService } from '../../../shared/services/video.service';
import { playHoverPreview, stopHoverPreview, claimHoverPreview, releaseHoverPreview } from '../../../shared/utils/video-hover-preview.util';

@Component({
  selector: 'app-video-card',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatIconModule, MatChipsModule, MatTooltipModule, MatProgressSpinnerModule],
  template: `
    <mat-card class="video-card"
              (mouseenter)="onHoverStart()" 
              (mouseleave)="onHoverEnd()">
      <div class="video-preview-container" (click)="onPlay()">
        <video #videoElement
               [src]="videoUrl || null"
               class="video-preview"
               muted
               [loop]="true"
               playsinline
               preload="metadata"
               (loadedmetadata)="onPreviewFrameReady()"
               (loadeddata)="onVideoLoaded()">
        </video>
        @if (previewLoading) {
          <div class="preview-loading">
            <mat-spinner diameter="32"></mat-spinner>
          </div>
        }
        <div class="play-overlay" [class.hidden]="isPlaying || previewFrameReady">
          <mat-icon>play_circle_filled</mat-icon>
        </div>
      </div>

      <mat-card-content>
        <h3 class="video-title">{{ video.filename }}</h3>
        
        <div class="reasoning-content" [class.expanded]="isExpanded">
          <mat-icon class="reasoning-icon">psychology</mat-icon>
          <p>{{ video.reasoning_content }}</p>
        </div>
        @if (video.reasoning_content && video.reasoning_content.length > 200) {
          <button class="expand-toggle" (click)="toggleExpand($event)">
            <mat-icon>{{ isExpanded ? 'expand_less' : 'expand_more' }}</mat-icon>
            {{ isExpanded ? 'See less' : 'See more' }}
          </button>
        }

        <div class="video-metadata">
          <span class="metadata-item">
            <mat-icon>movie</mat-icon>
            Segment {{ video.segment_number }}/{{ video.total_segments }}
          </span>
          <span class="metadata-item" [matTooltip]="video.original_video">
            <mat-icon>schedule</mat-icon>
            @if (video.segment_start_sec != null && video.segment_end_sec != null) {
              {{ formatVideoTime(video.segment_start_sec) }}–{{ formatVideoTime(video.segment_end_sec) }}
            } @else {
              {{ video.duration }}s
            }
          </span>
          @if (video.is_public) {
            <span class="metadata-item public">
              <mat-icon>public</mat-icon>
              Public
            </span>
          } @else {
            <span class="metadata-item private">
              <mat-icon>lock</mat-icon>
              Private
            </span>
          }
          <span class="metadata-item similarity-score">
            <mat-icon>star</mat-icon>
            {{ (video.similarity_score * 100).toFixed(1) }}%
          </span>
          @if (video.tokens_used != null && video.tokens_used !== undefined) {
            <span class="metadata-item">
              <mat-icon>token</mat-icon>
              {{ video.tokens_used }} tokens
            </span>
          }
          @if ((video.cached_prompt_tokens ?? 0) > 0) {
            <span
              class="metadata-item cache-input"
              matTooltip="Input tokens from prefix/KV cache (API field prompt_tokens_details; multimodal input)."
              matTooltipPosition="above">
              <mat-icon>bolt</mat-icon>
              {{ video.cached_prompt_tokens }} cached input
            </span>
          }
          @if (video.camera_id && video.camera_id.trim()) {
            <span class="metadata-item">
              <mat-icon>videocam</mat-icon>
              {{ video.camera_id }}
            </span>
          }
          @if (video.capture_type && video.capture_type.trim()) {
            <span class="metadata-item">
              <mat-icon>category</mat-icon>
              {{ video.capture_type }}
            </span>
          }
          @if (video.location && video.location.trim()) {
            <span class="metadata-item">
              <mat-icon>location_on</mat-icon>
              {{ video.location }}
            </span>
          }
        </div>

        @if (video.tags && video.tags.length > 0) {
          <div class="tags-container">
            <mat-chip-set class="tags">
              @for (tag of video.tags; track tag) {
                <mat-chip>{{ tag }}</mat-chip>
              }
            </mat-chip-set>
          </div>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .video-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      cursor: default;
      transition: all 0.3s ease;
      overflow: hidden;
      position: relative;
      
      &:hover {
        transform: translateY(-5px);
        box-shadow: var(--shadow-hover);
        border-color: var(--border-hover);
        background: var(--bg-card-hover);
      }
    }

    .video-preview-container {
      position: relative;
      width: 100%;
      height: 200px;
      background: #000;
      overflow: hidden;
      cursor: pointer !important;
    }

    .video-preview-container * {
      cursor: pointer !important;
    }

    .preview-loading {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.45);
      pointer-events: none;
      z-index: 2;
    }

    .video-preview {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .play-overlay {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.4);
      transition: opacity 0.3s ease;
      pointer-events: none;
      opacity: 1;
      
      &.hidden {
        opacity: 0;
      }
      
      mat-icon {
        font-size: 4rem;
        width: 4rem;
        height: 4rem;
        color: var(--text-primary);
        filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.5));
      }
    }

    ::ng-deep .mat-mdc-card-content {
      padding: 1rem;
    }

    .video-title {
      color: var(--text-primary);
      font-family: var(--font-name);
      font-size: 0.8rem;
      font-weight: 400;
      letter-spacing: 0.01em;
      margin: 0 0 1rem 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .reasoning-content {
      display: flex;
      gap: 0.75rem;
      align-items: flex-start;
      background: rgba(6, 255, 165, 0.05);
      border: 1px solid rgba(6, 255, 165, 0.2);
      border-radius: 12px;
      padding: 0.75rem;
      margin-bottom: 0.5rem;
      cursor: text;
      user-select: text;
      -webkit-user-select: text;
      
      .reasoning-icon {
        color: rgba(6, 255, 165, 0.8);
        font-size: 1.25rem;
        flex-shrink: 0;
      }
      
      p {
        margin: 0;
        color: var(--text-secondary);
        font-size: 0.9rem;
        line-height: 1.5;
        display: -webkit-box;
        -webkit-line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
        transition: all 0.3s ease;
      }
      
      &.expanded p {
        -webkit-line-clamp: unset;
        display: block;
      }
    }
    
    .expand-toggle {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      background: transparent;
      border: none;
      color: rgba(6, 255, 165, 0.8);
      font-size: 0.8rem;
      cursor: pointer;
      padding: 0.25rem 0.5rem;
      margin-bottom: 0.75rem;
      border-radius: 4px;
      transition: all 0.2s ease;
      
      mat-icon {
        font-size: 1rem;
        width: 1rem;
        height: 1rem;
      }
      
      &:hover {
        background: rgba(6, 255, 165, 0.1);
        color: rgba(6, 255, 165, 1);
      }
    }

    .video-metadata {
      display: flex;
      flex-wrap: nowrap;
      gap: 0.5rem;
      margin-bottom: 1rem;
      overflow-x: auto;
      overflow-y: hidden;
      
      /* Hide scrollbar but keep functionality */
      scrollbar-width: none;
      -ms-overflow-style: none;
      &::-webkit-scrollbar {
        display: none;
      }
      
      .metadata-item {
        display: flex;
        align-items: center;
        gap: 0.25rem;
        font-size: 0.8rem;
        color: var(--text-secondary);
        background: var(--bg-card);
        padding: 0.25rem 0.6rem;
        border-radius: 8px;
        white-space: nowrap;
        flex-shrink: 0;
        
        mat-icon {
          font-size: 0.95rem;
          width: 0.95rem;
          height: 0.95rem;
        }
        
        &.public {
          background: rgba(6, 255, 165, 0.1);
          color: var(--accent-success);
          border: 1px solid var(--accent-success);
        }
        
        &.private {
          background: rgba(255, 193, 7, 0.1);
          color: var(--accent-warning);
          border: 1px solid var(--accent-warning);
        }
        
        &.similarity-score {
          background: rgba(115, 200, 253, 0.15); /* lightblue-400 with opacity */
          color: #73c8fd; /* lightblue-400 */
          border: 1px solid rgba(115, 200, 253, 0.4); /* lightblue-400 with opacity */
          font-weight: 600;
          
          mat-icon {
            color: #73c8fd; /* lightblue-400 */
          }
        }

        &.cache-input {
          border-color: rgba(234, 179, 8, 0.35);
          color: rgba(234, 179, 8, 0.95);

          mat-icon {
            color: rgba(234, 179, 8, 0.95);
          }
        }
      }
    }

    .tags-container {
      margin-bottom: 0;
    }

    .tags {
      ::ng-deep mat-chip {
        background: var(--bg-secondary);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
        font-size: 0.75rem;
        transition: background 0.3s ease, color 0.3s ease, border-color 0.3s ease;
        
        .mdc-evolution-chip__text-label {
          color: var(--text-primary) !important;
        }
      }
    }

    ::ng-deep .mat-mdc-card-content {
      padding: 1rem;
      padding-bottom: 1rem !important;
    }
  `]
})
export class VideoCardComponent implements OnInit {
  private videoService = inject(VideoService);
  
  @Input({ required: true }) video!: VideoSearchResult;
  @Output() play = new EventEmitter<VideoSearchResult>();
  @ViewChild('videoElement') videoElement!: ElementRef<HTMLVideoElement>;

  isPlaying = false;
  isVideoLoaded = false;
  isExpanded = false;
  previewLoading = false;
  previewFrameReady = false;
  videoUrl: string = '';
  private hoverAbort?: AbortController;

  ngOnInit() {
    const token = localStorage.getItem('video_lab_token');
    if (token && this.video.source?.trim()) {
      this.videoUrl = this.videoService.getStreamUrl(this.video.source, token);
    }
  }

  onPlay() {
    this.play.emit(this.video);
  }

  toggleExpand(event: Event) {
    event.stopPropagation(); // Prevent card click
    this.isExpanded = !this.isExpanded;
  }

  formatVideoTime(seconds: number): string {
    if (seconds == null || Number.isNaN(seconds)) {
      return '?';
    }
    const total = Math.floor(seconds);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    if (hours > 0) {
      return `${hours}:${remMins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  onPreviewFrameReady() {
    this.previewFrameReady = true;
  }

  onVideoLoaded() {
    this.isVideoLoaded = true;
    if (this.previewLoading) {
      this.previewLoading = false;
    }
  }

  async onHoverStart() {
    const video = this.videoElement?.nativeElement;
    if (!video || !this.videoUrl) return;

    claimHoverPreview(video);

    this.hoverAbort?.abort();
    this.hoverAbort = new AbortController();
    const signal = this.hoverAbort.signal;

    this.previewLoading = true;
    const played = await playHoverPreview(video, this.videoUrl, { signal });
    if (!signal.aborted) {
      this.previewLoading = false;
      this.isPlaying = played;
      if (played) {
        claimHoverPreview(video);
      }
    }
  }

  onHoverEnd() {
    this.hoverAbort?.abort();
    this.hoverAbort = undefined;
    this.previewLoading = false;

    const video = this.videoElement?.nativeElement;
    if (!video) return;
    stopHoverPreview(video);
    releaseHoverPreview(video);
    this.isPlaying = false;
  }
}

