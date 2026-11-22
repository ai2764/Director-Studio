/** Crisp geometric Linear-style frame mark for Director Studio. */
export function DirectorStudioMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[18px] w-[18px] shrink-0 text-ink"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        className="director-mark-frame"
        d="M8.5 3.75H5.75a2 2 0 0 0-2 2V8.5M15.5 3.75h2.75a2 2 0 0 1 2 2V8.5M20.25 15.5v2.75a2 2 0 0 1-2 2H15.5M8.5 20.25H5.75a2 2 0 0 1-2-2V15.5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        className="director-mark-letter"
        fillRule="evenodd"
        clipRule="evenodd"
        d="M8 7.5h4.25a4.5 4.5 0 1 1 0 9H8v-9Zm2.75 2.5v4h1.4a2 2 0 1 0 0-4h-1.4Z"
        fill="currentColor"
      />
    </svg>
  );
}
