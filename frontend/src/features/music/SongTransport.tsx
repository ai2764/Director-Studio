import { useEffect, useRef, useState } from "react";
import { useProject } from "../../shared/project/ProjectContext";
import { getSongSegments, songAudioUrl, type SongSegmentsDocument, type SegmentSelection, type SongSegment } from "./api";

const time = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;

export function SongTransport({ active, onSelectionChange }: {
  active: boolean;
  onSelectionChange: (selection: SegmentSelection | undefined) => void;
}) {
  const { projectId, project } = useProject();
  const master = project?.music_master;
  const audio = useRef<HTMLAudioElement>(null);
  const bounds = useRef<SongSegment | null>(null);
  const panDrag = useRef<{ x: number; y: number; start: number; moved: boolean } | null>(null);
  const suppressAnchorClick = useRef(false);
  const selectionCallback = useRef(onSelectionChange);
  selectionCallback.current = onSelectionChange;
  const [document, setDocument] = useState<SongSegmentsDocument | null>(null);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState(false);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [mobileViewport, setMobileViewport] = useState(false);
  const [panStart, setPanStart] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const saved = (event: Event) => {
      if ((event as CustomEvent).detail === projectId) setRevision((value) => value + 1);
    };
    window.addEventListener("song-segments-changed", saved);
    return () => window.removeEventListener("song-segments-changed", saved);
  }, [projectId]);

  useEffect(() => {
    let live = true;
    const player = audio.current;
    player?.pause();
    bounds.current = null;
    setDocument(null);
    setIndex(0);
    setSelected(false);
    setPosition(0);
    setPanStart(null);
    setError("");
    selectionCallback.current(undefined);
    if (projectId) getSongSegments(projectId).then((state) => {
      if (!live) return;
      if (state.master_stale || (state.document && state.document.master_sha256 !== master?.content_sha256)) {
        setError("The song has changed. Review and save segments in Assets → Music.");
      } else setDocument(state.document);
    }).catch((cause) => { if (live) setError(String(cause)); });
    return () => { live = false; player?.pause(); };
  }, [projectId, master?.content_sha256, revision]);

  useEffect(() => { if (!active) audio.current?.pause(); }, [active]);

  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 780px)");
    if (!media) {
      const isMobile = window.innerWidth <= 780;
      setMobileViewport(isMobile);
      setZoom(isMobile);
      return;
    }
    const updateViewport = () => {
      setMobileViewport(media.matches);
      setZoom(media.matches);
      setPanStart(null);
    };
    updateViewport();
    media.addEventListener("change", updateViewport);
    return () => media.removeEventListener("change", updateViewport);
  }, []);

  // A frame clock keeps short auditions from running past their end between media events.
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => { updatePosition(); frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, loop]);

  const rows = document?.segments || [];
  const current = rows[index];
  const duration = master?.duration_s || 0;
  const defaultViewStart = current ? Math.max(0, current.start_s - 10) : 0;
  const zoomSpan = current ? Math.min(duration || 30, Math.max(30, current.end_s - defaultViewStart)) : 30;
  const viewStart = zoom && current
    ? Math.max(0, Math.min(panStart ?? defaultViewStart, Math.max(0, duration - zoomSpan)))
    : 0;
  const viewEnd = zoom && current ? Math.min(duration, viewStart + zoomSpan) : duration;
  const span = Math.max(1, viewEnd - viewStart);

  function updatePosition() {
    const player = audio.current;
    if (!player) return;
    const clip = bounds.current;
    if (clip && player.currentTime >= clip.end_s) {
      if (loop) player.currentTime = clip.start_s;
      else { player.pause(); player.currentTime = clip.end_s; bounds.current = null; }
    }
    setPosition(player.currentTime);
  }

  function choose(next: number) {
    const row = rows[next];
    if (!row || !document) return;
    audio.current?.pause();
    bounds.current = null;
    if (audio.current) audio.current.currentTime = row.start_s;
    setPosition(row.start_s);
    setIndex(next);
    setSelected(true);
    setPanStart(null);
    onSelectionChange({ revision: document.revision, ids: [row.id] });
  }

  async function play(clip: SongSegment | null) {
    const player = audio.current;
    if (!player) return;
    bounds.current = clip;
    if (clip) player.currentTime = clip.start_s;
    try { await player.play(); } catch (cause) { setError(`Could not play song: ${String(cause)}`); }
  }

  return <section className="mv-song-transport" aria-label="Song player">
    <audio ref={audio} preload="metadata" src={projectId && master ? `${songAudioUrl(projectId)}?v=${master.content_sha256}` : undefined}
      onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={updatePosition} />
    <div className="mv-transport-controls">
      <strong className="mv-song-name" title={master?.filename}>{master?.filename || "Upload a song in Assets → Music"}</strong>
      <button type="button" className="btn secondary" disabled={!master} onClick={() => playing ? audio.current?.pause() : void play(null)}>{playing ? "Pause" : "Play song"}</button>
      <span className="mv-song-time">{time(position)} / {time(duration)}</span>
      <select aria-label="Choose segment" value={current?.id || ""} disabled={!rows.length} onChange={(event) => choose(rows.findIndex((row) => row.id === event.target.value))}>
        {!rows.length && <option value="">Import segments in Assets → Music</option>}
        {rows.map((row, i) => <option key={row.id} value={row.id}>{i + 1}. {time(row.start_s)} · {row.text}</option>)}
      </select>
      <button type="button" className="btn secondary" aria-pressed={zoom} disabled={!current} onClick={() => setZoom(!zoom)}>{zoom ? "Full song" : "Zoom"}</button>
      <button type="button" className="btn secondary" aria-pressed={loop} onClick={() => setLoop(!loop)}>Loop segment</button>
    </div>
    <div className="mv-song-timeline">
      <input type="range" aria-label="Song position" min={viewStart} max={viewEnd || 1} step="0.01" value={Math.max(viewStart, Math.min(position, viewEnd))} disabled={!master}
        onChange={(event) => { const value = Number(event.target.value); bounds.current = null; if (audio.current) audio.current.currentTime = value; setPosition(value); }} />
      <div className={`mv-song-anchors${mobileViewport && zoom ? " mobile-pan" : ""}`}
        onPointerDown={(event) => {
          if (!mobileViewport || !zoom || event.button !== 0) return;
          panDrag.current = { x: event.clientX, y: event.clientY, start: viewStart, moved: false };
        }}
        onPointerMove={(event) => {
          const drag = panDrag.current;
          if (!drag || !mobileViewport || !zoom) return;
          const delta = event.clientX - drag.x;
          const deltaY = event.clientY - drag.y;
          if (Math.abs(deltaY) > 4 && Math.abs(deltaY) > Math.abs(delta)) {
            panDrag.current = null;
            return;
          }
          if (Math.abs(delta) > 4) drag.moved = true;
          if (!drag.moved) return;
          const width = event.currentTarget.getBoundingClientRect().width;
          const maxStart = Math.max(0, duration - zoomSpan);
          setPanStart(Math.max(0, Math.min(maxStart, drag.start - delta / Math.max(1, width) * zoomSpan)));
        }}
        onPointerUp={() => {
          if (panDrag.current?.moved) suppressAnchorClick.current = true;
          panDrag.current = null;
        }}
        onPointerCancel={() => { panDrag.current = null; }}
        onClickCapture={(event) => {
          if (!suppressAnchorClick.current) return;
          event.preventDefault();
          event.stopPropagation();
          suppressAnchorClick.current = false;
        }}>
        {rows.map((row, i) => row.start_s >= viewStart && row.start_s <= viewEnd && <button key={row.id} type="button"
          className={i === index ? "active" : ""} style={{ left: `${(row.start_s - viewStart) / span * 100}%` }}
          aria-label={`Select segment ${i + 1}: ${row.text}`} title={`${i + 1} · ${time(row.start_s)}–${time(row.end_s)} · ${row.text}`} onClick={() => choose(i)} />)}
      </div>
      {mobileViewport && zoom && <small className="mv-pan-hint">Swipe timeline left or right to browse segments</small>}
    </div>
    {current && <div className="mv-lyric-strip">
      {[index - 1, index, index + 1].map((i, slot) => <div key={slot} className={`mv-lyric-card${slot === 1 ? " current" : ""}`}>
        {rows[i] ? <>
          <button type="button" className="mv-lyric-select" onClick={() => choose(i)} title={rows[i].text}>
            <small>{slot === 0 ? "Previous" : slot === 1 ? "Current" : "Next"} · {i + 1} · {time(rows[i].start_s)}–{time(rows[i].end_s)}</small>
            <span>{rows[i].text || "Instrumental"}</span>
          </button>
          <button type="button" className="mv-audition" aria-label={`Audition segment ${i + 1}`} title="Play this segment" onClick={() => void play(rows[i])}>▶</button>
        </> : <small>{slot === 0 ? "Start of song" : "End of song"}</small>}
      </div>)}
      <div className="mv-discussion-focus">
        <span>{selected ? `Discussing segment ${index + 1}` : "Discussing whole song"}</span>
        {selected && <button type="button" onClick={() => { setSelected(false); onSelectionChange(undefined); }}>Clear selection</button>}
      </div>
    </div>}
    {error && <p role="alert" className="mv-transport-error">{error}</p>}
  </section>;
}
