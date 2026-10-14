"use client";

import { useEffect, useRef, useState } from "react";
import { CarFront, Home, Video } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import {
  clipUrl,
  withWindow,
  type AlertItem,
  type Camera,
  type FeedSummary,
  type TimeFilter,
} from "@/lib/api";

const WINDOWS: { id: TimeFilter; label: string }[] = [
  { id: "1h", label: "1h" },
  { id: "24h", label: "24h" },
  { id: "7d", label: "7d" },
  { id: "all", label: "All" },
];

const CAM_META: Record<
  string,
  { icon: typeof Home; blurb: string; attention: string; pane: string }
> = {
  indoor: {
    icon: Video,
    blurb: "Ceiling — display only",
    attention: "Indoor has no occupancy alerts in v1. Watch the clip only.",
    pane: "bg-sky-500/5",
  },
  dashcam: {
    icon: CarFront,
    blurb: "In the car",
    attention: "Hazards in this window for the dashcam.",
    pane: "bg-amber-500/5",
  },
  house: {
    icon: Home,
    blurb: "Outside the house",
    attention: "Presence at the house in this window.",
    pane: "bg-emerald-500/5",
  },
};

function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function kindVariant(kind: string): "house" | "car" | "indoor" | "default" {
  if (kind === "house") return "house";
  if (kind === "car") return "car";
  if (kind === "indoor") return "indoor";
  return "default";
}

export function Board() {
  const [team, setTeam] = useState("team");
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[] | null>(null);
  const [summaries, setSummaries] = useState<FeedSummary[]>([]);
  const [selectedId, setSelectedId] = useState("house");
  const [activeAlert, setActiveAlert] = useState<string | null>(null);
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("7d");
  const [date, setDate] = useState("");
  const [error, setError] = useState("");
  const [player, setPlayer] = useState<{ source: string; start: number } | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const selected = cameras.find((c) => c.id === selectedId) || cameras[0];
  const selectedMeta = CAM_META[selectedId] || CAM_META.house;
  const visibleAlerts = (alerts || []).filter((a) => a.card_id === selectedId);
  const visibleSummaries = summaries.filter((s) => s.card_id === selectedId);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setAlerts(null);
      setError("");
      setPlayer(null);
      try {
        const cams = await fetch(withWindow("/api/cameras", timeFilter, date)).then((r) => r.json());
        if (cancelled) return;
        setTeam(cams.team || "team");
        setHealthy(!!cams.healthy);
        const list: Camera[] = cams.cameras || [];
        setCameras(list);
        const current = list.find((c) => c.id === selectedId) || list[0];
        if (current?.source) {
          setPlayer({ source: current.source, start: current.start_sec || 0 });
        } else {
          setPlayer(null);
        }
      } catch {
        if (!cancelled) {
          setHealthy(false);
          setError("Could not load cameras.");
        }
      }
      try {
        const data = await fetch(withWindow("/api/alerts", timeFilter, date)).then((r) => r.json());
        if (cancelled) return;
        setAlerts(data.alerts || []);
        setSummaries(data.summaries || []);
      } catch {
        if (!cancelled) {
          setAlerts([]);
          setSummaries([]);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [timeFilter, date]);

  function playClip(source: string, startSec: number) {
    if (!source) return;
    setPlayer({ source, start: startSec });
  }

  function selectCamera(id: string) {
    setSelectedId(id);
    setActiveAlert(null);
    const cam = cameras.find((c) => c.id === id);
    if (cam?.source) playClip(cam.source, cam.start_sec || 0);
    else setPlayer(null);
  }

  function onAlert(a: AlertItem) {
    setSelectedId(a.card_id);
    setActiveAlert(a.id);
    playClip(a.source, a.start_sec);
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border bg-card/80 px-4 py-3 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold tracking-wide">Home Watch</div>
            <p className="mt-1 max-w-xl text-xs text-muted-foreground">
              Don&apos;t scrub three feeds. This board ranks house and dashcam moments that need a
              look, then jumps you to the clip.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline">{team}</Badge>
            <Badge variant={healthy ? "house" : "car"}>
              {healthy === null ? "checking" : healthy ? "healthy" : "degraded"}
            </Badge>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Window</span>
          {WINDOWS.map((w) => (
            <Button
              key={w.id}
              size="sm"
              variant={!date && timeFilter === w.id ? "default" : "outline"}
              onClick={() => {
                setDate("");
                setTimeFilter(w.id);
              }}
            >
              {w.label}
            </Button>
          ))}
          <Input
            type="date"
            className="w-[10.5rem]"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
      </header>

      <div className="grid flex-1 grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)_minmax(320px,400px)]">
        <aside className="border-b border-border p-3 lg:border-b-0 lg:border-r">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
            Cameras
          </div>
          <div className="flex gap-2 lg:flex-col">
            {(cameras.length ? cameras : [{ id: "house", label: "House exterior", camera_id: "" }]).map(
              (cam) => {
                const meta = CAM_META[cam.id] || CAM_META.house;
                const Icon = meta.icon;
                const on = selectedId === cam.id;
                return (
                  <Button
                    key={cam.id}
                    variant={on ? "default" : "outline"}
                    aria-current={on ? "page" : undefined}
                    className={cn(
                      "h-auto w-full justify-start py-2",
                      on && "ring-2 ring-primary ring-offset-2 ring-offset-background"
                    )}
                    onClick={() => selectCamera(cam.id)}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="flex flex-col items-start text-left">
                      <span>{cam.label}</span>
                      <span className="text-[10px] font-normal opacity-80">{meta.blurb}</span>
                    </span>
                  </Button>
                );
              }
            )}
          </div>
        </aside>

        <section className="min-w-0 p-3">
          <div className="sticky top-0 z-10 space-y-2 bg-background/95 pb-2 backdrop-blur">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">{selected?.label || "Camera"}</h2>
              <span className="font-mono text-[10px] text-muted-foreground">
                {selected?.camera_id}
              </span>
            </div>
            <Card className="overflow-hidden">
              {player?.source && selected?.ok ? (
                <video
                  ref={videoRef}
                  className="aspect-video w-full bg-black"
                  muted
                  controls
                  playsInline
                  preload="metadata"
                  src={clipUrl(player.source)}
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget;
                    const t = player.start || 0;
                    if (t > 0 && t < (v.duration || t + 1)) v.currentTime = t;
                    v.play().catch(() => undefined);
                  }}
                />
              ) : (
                <div className="flex aspect-video items-center justify-center px-6 text-center text-sm text-muted-foreground">
                  {error || "Nothing in this time window for this camera."}
                </div>
              )}
            </Card>
            {selected?.ok && selected?.reasoning ? (
              <Card>
                <CardHeader>
                  <CardTitle className="text-muted-foreground">Camera summary</CardTitle>
                </CardHeader>
                <CardContent className="text-sm leading-relaxed">{selected.reasoning}</CardContent>
              </Card>
            ) : null}
          </div>
        </section>

        <aside
          className={cn(
            "border-t border-border lg:border-l lg:border-t-0",
            selectedMeta.pane
          )}
        >
          <div className="p-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Needs attention
              </h3>
              <Badge variant={kindVariant(selectedId === "dashcam" ? "car" : selectedId)}>
                {selected?.label || selectedId}
              </Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{selectedMeta.attention}</p>
          </div>
          <Separator />
          <ScrollArea className="h-[calc(100vh-11rem)]">
            <div className="space-y-3 p-3">
              {selectedId === "indoor" ? (
                <p className="text-sm text-muted-foreground">{selectedMeta.attention}</p>
              ) : alerts === null ? (
                <p className="text-sm text-muted-foreground">Loading alerts…</p>
              ) : (
                <>
                  {visibleSummaries
                    .filter((s) => s.text)
                    .map((s) => (
                      <Card key={s.kind} className="border-primary/40">
                        <CardHeader className="flex-row items-center justify-between space-y-0">
                          <CardTitle>{s.label} summary</CardTitle>
                          <Badge variant={kindVariant(s.kind)}>{s.label}</Badge>
                        </CardHeader>
                        <CardContent className="text-sm leading-relaxed text-muted-foreground">
                          {s.text}
                        </CardContent>
                      </Card>
                    ))}
                  {visibleAlerts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      Nothing in this time window for {selected?.label || "this camera"}.
                    </p>
                  ) : (
                    visibleAlerts.map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => onAlert(a)}
                        className={cn(
                          "w-full rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary",
                          activeAlert === a.id && "border-primary ring-2 ring-primary/60"
                        )}
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <Badge variant={kindVariant(a.kind)}>{a.label}</Badge>
                          <span className="text-[11px] text-muted-foreground">
                            {fmtTime(a.start_sec)} · clip
                          </span>
                        </div>
                        <div className="text-sm leading-snug">{a.summary}</div>
                      </button>
                    ))
                  )}
                </>
              )}
            </div>
          </ScrollArea>
        </aside>
      </div>
    </div>
  );
}
