import {
  Box,
  Check,
  FileText,
  Image,
  Images,
  LayoutGrid,
  LayoutPanelLeft,
  MessageSquare,
  Mic,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Wand2,
  X,
  Users,
  type LucideIcon,
} from "lucide-react";

export type StudioIconName =
  | "library"
  | "actors"
  | "scenes"
  | "props"
  | "layouts"
  | "voices"
  | "upload"
  | "refresh"
  | "edit"
  | "delete"
  | "search"
  | "file"
  | "close"
  | "check"
  | "plus"
  | "wand"
  | "chat"
  | "assist-references"
  | "play";

const ICONS: Record<StudioIconName, LucideIcon> = {
  "assist-references": Images,
  play: Play,
  wand: Wand2,
  chat: MessageSquare,
  close: X,
  check: Check,
  plus: Plus,
  library: LayoutGrid,
  actors: Users,
  scenes: Image,
  props: Box,
  layouts: LayoutPanelLeft,
  voices: Mic,
  upload: Upload,
  refresh: RefreshCw,
  edit: Pencil,
  delete: Trash2,
  search: Search,
  file: FileText,
};

export function StudioIcon({ name, size = 18 }: { name: StudioIconName; size?: number }) {
  const Icon = ICONS[name];
  return (
    <Icon
      className="studio-icon shrink-0"
      size={size}
      strokeWidth={1.75}
      aria-hidden={true}
      focusable="false"
    />
  );
}
