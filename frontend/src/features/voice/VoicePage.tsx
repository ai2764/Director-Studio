import { LibraryPage } from "../library/LibraryPage";

/** Dedicated Voice tab — same import/list as Library → Voices. */
export function VoicePage() {
  return <LibraryPage lockedKind="voices" />;
}
