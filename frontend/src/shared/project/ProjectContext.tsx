import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createProject,
  listProjects,
  type Project,
} from "../../features/director/api";
import type { ProjectMode } from "../api/types";

const STORAGE_KEY = "ds.activeProjectId";

type ProjectContextValue = {
  projects: Project[];
  projectId: string | null;
  project: Project | null;
  loading: boolean;
  error: string | null;
  setProjectId: (id: string | null) => void;
  refreshProjects: () => Promise<void>;
  createAndSelect: (
    name: string,
    scriptText?: string,
    mode?: ProjectMode,
  ) => Promise<Project>;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    setError(null);
    try {
      const items = await listProjects();
      setProjects(items);
      setProjectIdState((cur) => {
        if (cur && items.some((p) => p.id === cur)) return cur;
        const next = items[0]?.id ?? null;
        try {
          if (next) localStorage.setItem(STORAGE_KEY, next);
          else localStorage.removeItem(STORAGE_KEY);
        } catch {
          /* ignore */
        }
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const setProjectId = useCallback((id: string | null) => {
    setProjectIdState(id);
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const createAndSelect = useCallback(
    async (name: string, scriptText = "", mode: ProjectMode = "director") => {
      const p = await createProject({
        name: name.trim() || "Untitled Project",
        script_text: scriptText,
        mode,
      });
      await refreshProjects();
      setProjectId(p.id);
      return p;
    },
    [refreshProjects, setProjectId],
  );

  const project = useMemo(
    () => projects.find((p) => p.id === projectId) ?? null,
    [projects, projectId],
  );

  const value = useMemo(
    () => ({
      projects,
      projectId,
      project,
      loading,
      error,
      setProjectId,
      refreshProjects,
      createAndSelect,
    }),
    [
      projects,
      projectId,
      project,
      loading,
      error,
      setProjectId,
      refreshProjects,
      createAndSelect,
    ],
  );

  return (
    <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
  );
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) {
    throw new Error("useProject must be used within ProjectProvider");
  }
  return ctx;
}
