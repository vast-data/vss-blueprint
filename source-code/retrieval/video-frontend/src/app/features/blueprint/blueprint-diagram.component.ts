import { Component, OnInit } from '@angular/core';

@Component({
  selector: 'app-blueprint-diagram',
  standalone: true,
  imports: [],
  template: '<p>Redirecting to blueprint...</p>',
})
export class BlueprintDiagramComponent implements OnInit {
  ngOnInit(): void {
    window.location.href = '/assets/blueprint.html';
  }
}
