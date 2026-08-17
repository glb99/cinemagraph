import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorText, Hint } from "@/components/ui/field";
import { errorMessage, libraryFileUrl } from "@/lib/api";

/** The canvas's own pixel buffer, fixed and independent of how wide it renders
 * -- peaks are computed once per column of *this* buffer, while pointer
 * coordinates arrive in CSS-box space and are converted separately (see
 * `selectionFromPointer`). Conflating the two is a real bug this UI already hit
 * once: the canvas typically renders ~720px wide against a 680px buffer. */
const WAVEFORM_WIDTH = 680;
const WAVEFORM_HEIGHT = 80;

/** min/max per pixel column -- the standard lightweight waveform summary.
 * Computed once per loaded asset and cached, so every redraw (drag frame,
 * manual field edit, theme repaint) is just cheap fillRect calls rather than
 * another scan of the raw PCM data. */
type Peaks = Array<[number, number]>;

function computePeaks(buffer: AudioBuffer): Peaks {
  const data = buffer.getChannelData(0);
  const samplesPerPixel = Math.max(1, Math.floor(data.length / WAVEFORM_WIDTH));
  const peaks: Peaks = [];
  for (let x = 0; x < WAVEFORM_WIDTH; x++) {
    let min = 0;
    let max = 0;
    const start = x * samplesPerPixel;
    for (let i = 0; i < samplesPerPixel; i++) {
      const value = data[start + i] ?? 0;
      if (value < min) min = value;
      if (value > max) max = value;
    }
    peaks.push([min, max]);
  }
  return peaks;
}

export interface RepaintWaveformProps {
  /** The song being repainted; null until one is picked. */
  assetId: string | null;
  /** Seconds, as typed -- kept as strings so a partially-typed value survives
   * the round trip through the number inputs beside this canvas. */
  start: string;
  end: string;
  onChange: (start: string, end: string) => void;
}

/** Drag-selectable repaint region, drawn with the platform's own canvas and
 * Web Audio API (`decodeAudioData`) -- no charting or waveform library. Typing
 * into the start/end fields and dragging here are both first class: each keeps
 * the other in sync. */
export function RepaintWaveform({ assetId, start, end, onChange }: RepaintWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const dragStartXRef = useRef<number | null>(null);
  const [buffer, setBuffer] = useState<AudioBuffer | null>(null);
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!assetId) {
      setBuffer(null);
      setPeaks(null);
      return;
    }
    let ignore = false;
    setError(null);
    (async () => {
      try {
        const response = await fetch(libraryFileUrl(assetId));
        const arrayBuffer = await response.arrayBuffer();
        // Created lazily on first use, standard practice for AudioContext
        // (browsers refuse to start one before a user gesture in some modes).
        audioContextRef.current ??= new AudioContext();
        const decoded = await audioContextRef.current.decodeAudioData(arrayBuffer);
        if (ignore) return;
        setBuffer(decoded);
        setPeaks(computePeaks(decoded));
      } catch (err) {
        if (!ignore) setError(`Couldn't load waveform: ${errorMessage(err)}`);
      }
    })();
    return () => {
      ignore = true;
    };
  }, [assetId]);

  const startSeconds = Number.parseFloat(start) || 0;
  const endRaw = Number.parseFloat(end);

  // Redraw on every input that can change what's on screen: the loaded peaks,
  // and either end of the selection.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    context.clearRect(0, 0, WAVEFORM_WIDTH, WAVEFORM_HEIGHT);
    if (!peaks || !buffer) return;

    const styles = getComputedStyle(canvas);
    const mid = WAVEFORM_HEIGHT / 2;
    context.fillStyle = styles.getPropertyValue("--waveform").trim();
    peaks.forEach(([min, max], x) => {
      context.fillRect(x, mid + min * mid, 1, Math.max(1, (max - min) * mid));
    });

    const duration = buffer.duration;
    const endSeconds = endRaw < 0 || Number.isNaN(endRaw) ? duration : endRaw;
    const x1 = Math.max(0, Math.min(WAVEFORM_WIDTH, (startSeconds / duration) * WAVEFORM_WIDTH));
    const x2 = Math.max(0, Math.min(WAVEFORM_WIDTH, (endSeconds / duration) * WAVEFORM_WIDTH));
    context.fillStyle = styles.getPropertyValue("--waveform-selection").trim();
    context.fillRect(x1, 0, x2 - x1, WAVEFORM_HEIGHT);
  }, [peaks, buffer, startSeconds, endRaw]);

  const selectionFromPointer = useCallback(
    (pixelX1: number, pixelX2: number) => {
      const canvas = canvasRef.current;
      if (!canvas || !buffer) return;
      // Pointer offsets are relative to the canvas's *rendered* CSS box, which
      // is not WAVEFORM_WIDTH -- measure it rather than assuming.
      const renderedWidth = canvas.getBoundingClientRect().width;
      const lo = Math.max(0, Math.min(pixelX1, pixelX2));
      const hi = Math.min(renderedWidth, Math.max(pixelX1, pixelX2));
      const nextStart = (lo / renderedWidth) * buffer.duration;
      // Dragging to the visible end of the track means "to the end" -- snapping
      // to the -1 sentinel there beats an oddly precise duration-minus-epsilon
      // that depends on how wide the canvas happened to render.
      const nextEnd = hi > renderedWidth * 0.99 ? -1 : (hi / renderedWidth) * buffer.duration;
      onChange(nextStart.toFixed(2), nextEnd === -1 ? "-1" : nextEnd.toFixed(2));
    },
    [buffer, onChange],
  );

  // The drag ends wherever the pointer happens to be, including outside the
  // canvas, so the release is watched on the window rather than the element.
  useEffect(() => {
    const stopDrag = () => {
      dragStartXRef.current = null;
    };
    window.addEventListener("mouseup", stopDrag);
    return () => window.removeEventListener("mouseup", stopDrag);
  }, []);

  const previewSelection = () => {
    const context = audioContextRef.current;
    if (!buffer || !context) return;
    const endSeconds = endRaw < 0 || Number.isNaN(endRaw) ? buffer.duration : endRaw;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.start(0, startSeconds, Math.max(0.01, endSeconds - startSeconds));
  };

  return (
    <div className="space-y-2">
      <canvas
        ref={canvasRef}
        width={WAVEFORM_WIDTH}
        height={WAVEFORM_HEIGHT}
        aria-label="Repaint region"
        className="block h-20 w-full cursor-crosshair rounded-md border border-border bg-muted"
        onMouseDown={(event) => {
          if (!buffer) return;
          dragStartXRef.current = event.nativeEvent.offsetX;
        }}
        onMouseMove={(event) => {
          if (dragStartXRef.current === null) return;
          selectionFromPointer(dragStartXRef.current, event.nativeEvent.offsetX);
        }}
      />
      {!assetId && <Hint>Pick the song to repaint above to see its waveform.</Hint>}
      <ErrorText>{error}</ErrorText>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={previewSelection}
        disabled={!buffer}
      >
        ▶ Preview selection
      </Button>
    </div>
  );
}
