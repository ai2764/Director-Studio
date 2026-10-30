export type StudioIconName = "library" | "actors" | "scenes" | "props" | "layouts" | "voices" | "upload" | "refresh" | "edit" | "delete" | "search" | "file" | "close" | "check" | "plus" | "wand" | "chat";

const paths: Record<StudioIconName, string[]> = {
  wand: ["m4 17 11-11 4 4L8 21H4v-4Z", "m12 9 4 4", "M5 3v4", "M3 5h4", "M18 2v4", "M16 4h4", "M20 18v4", "M18 20h4"],
  chat: ["M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z", "M7 10h10", "M7 14h6"],
  close: ["m6 6 12 12", "M18 6 6 18"],
  check: ["m5 12 4 4L19 6"],
  plus: ["M12 5v14", "M5 12h14"],
  library: ["M4 4h6v7H4z", "M14 4h6v7h-6z", "M4 15h6v5H4z", "M14 15h6v5h-6z"],
  actors: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8", "M17 4a4 4 0 0 1 0 8", "M22 21v-2a4 4 0 0 0-3-3.87"],
  scenes: ["M3 5h18v14H3z", "m3 15 5-5 7 9", "m13 16 3-4 5 7", "M16 8h.01"],
  props: ["m12 3 9 5-9 5-9-5 9-5Z", "m3 8 0 9 9 5 9-5V8", "M12 13v9", "m7.5 5.5 9 5"],
  layouts: ["M3 3h18v18H3z", "M3 9h18", "M9 9v12"],
  voices: ["M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Z", "M5 10v2a7 7 0 0 0 14 0v-2", "M12 19v3", "M8 22h8"],
  upload: ["M12 16V3", "m7 8 5-5 5 5", "M4 16v4h16v-4"],
  refresh: ["M20 7v5h-5", "M4 17v-5h5", "M6.1 6a8 8 0 0 1 13.4 3L20 12", "M4 12l.5 3A8 8 0 0 0 18 18"],
  edit: ["m15 5 4 4", "m3 21 5-1L21 7a2.8 2.8 0 0 0-4-4L4 16l-1 5Z"],
  delete: ["M3 6h18", "M9 6V3h6v3", "m5 6 1 15h12l1-15", "M10 10v7", "M14 10v7"],
  search: ["M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13", "m16 16 5 5"],
  file: ["M14 2H5v20h14V7l-5-5Z", "M14 2v6h5", "M8 13h8", "M8 17h5"],
};

export function StudioIcon({ name, size = 18 }: { name: StudioIconName; size?: number }) {
  return <svg className="studio-icon" width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {paths[name].map((d, index) => <path key={index} d={d} />)}
  </svg>;
}
