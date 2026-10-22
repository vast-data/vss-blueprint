import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnDestroy,
  ViewChild,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-pill-rail',
  standalone: true,
  imports: [CommonModule, MatIconModule],
  template: `
    <div class="date-rail">
      <span class="date-label">{{ label }}</span>
      @if (showLeft()) {
        <button type="button" class="rail-arrow" (click)="nudge(-1)" aria-label="Scroll left">
          <mat-icon>chevron_left</mat-icon>
        </button>
      }
      <div class="rail-track">
        <div class="rail-pills" #scroller (scroll)="refresh()">
          <ng-content></ng-content>
        </div>
      </div>
      @if (showRight()) {
        <button type="button" class="rail-arrow" (click)="nudge(1)" aria-label="Scroll right">
          <mat-icon>chevron_right</mat-icon>
        </button>
      }
    </div>
  `,
  styles: [`
    :host {
      display: block;
      min-width: 0;
    }

    .date-rail {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      min-width: 0;
      padding-top: 0.85rem;
      border-top: 1px solid var(--border-color);
    }

    .date-label {
      color: var(--text-secondary);
      font-size: 0.95rem;
      font-weight: 500;
      white-space: nowrap;
      flex: 0 0 auto;
    }

    .rail-track {
      flex: 1;
      min-width: 0;
      border: 1px solid transparent;
      border-radius: 14px;
      background: var(--bg-secondary);
      overflow: hidden;
    }

    .rail-pills {
      display: flex;
      flex-wrap: nowrap;
      gap: 0.5rem;
      overflow-x: auto;
      scrollbar-width: none;
      padding: 0.4rem 0.55rem;
    }

    .rail-pills::-webkit-scrollbar {
      display: none;
    }

    .rail-arrow {
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      border: none;
      background: none;
      padding: 0;
      cursor: pointer;
      color: var(--accent-primary);

      mat-icon {
        width: 1.6rem;
        height: 1.6rem;
        font-size: 1.6rem;
        animation: arrow-blink 1.3s ease-in-out infinite;
      }
    }

    @keyframes arrow-blink {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.25; }
    }

    @media (prefers-reduced-motion: reduce) {
      .rail-arrow mat-icon {
        animation: none;
      }
    }
  `],
})
export class PillRailComponent implements AfterViewInit, OnDestroy {
  @Input() label = '';
  @ViewChild('scroller') scroller?: ElementRef<HTMLElement>;

  showLeft = signal(false);
  showRight = signal(false);

  private resizeObserver?: ResizeObserver;
  private mutationObserver?: MutationObserver;

  ngAfterViewInit() {
    const el = this.scroller?.nativeElement;
    if (!el) return;
    this.resizeObserver = new ResizeObserver(() => this.refresh());
    this.resizeObserver.observe(el);
    this.mutationObserver = new MutationObserver(() => this.refresh());
    this.mutationObserver.observe(el, { childList: true, subtree: true });
    this.refresh();
    queueMicrotask(() => this.refresh());
  }

  ngOnDestroy() {
    this.resizeObserver?.disconnect();
    this.mutationObserver?.disconnect();
  }

  refresh() {
    const el = this.scroller?.nativeElement;
    if (!el) return;
    const hidden = el.scrollWidth - el.clientWidth;
    this.showLeft.set(el.scrollLeft > 4);
    this.showRight.set(hidden - el.scrollLeft > 4);
  }

  nudge(direction: number) {
    const el = this.scroller?.nativeElement;
    if (!el) return;
    el.scrollBy({
      left: direction * Math.max(180, Math.round(el.clientWidth * 0.72)),
      behavior: 'smooth',
    });
  }
}
