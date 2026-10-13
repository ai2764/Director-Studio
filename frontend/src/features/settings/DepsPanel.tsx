import { useCallback, useEffect, useRef, useState } from "react";
import { GroupForm, type ConfigModel } from "../setup/ConfigForm";
import { useI18n } from "../setup/i18n";
import {
  ACTIVE_JOB, cancelDep, formatBytes, getDeps, installDeps, installWorkflow, restartComfyForDeps,
  type DepItem, type DepsStatus, type DepWorkflow,
} from "./depsApi";

const TEXT = {
  zh: {
    title: "工作流依赖",
    notice: "DS 不附带任何第三方模型或节点包。点击下载即由你本人从原作者处（Hugging Face / ModelScope / GitHub）下载，适用原作者的许可，请先阅读许可条款。",
    unlicensedWarn: "该节点包没有任何许可证（作者保留所有权利）。下载和使用风险由你自行判断，DS 不提供、不再分发它。",
    gatedWarn: "该模型需要先在 Hugging Face 上同意条款，并在下方填写 Hugging Face Token。",
    statusOnly: "当前 ComfyUI 不是内置（托管）模式：这里只显示状态和下载链接，请在运行 ComfyUI 的机器上自行安装。",
    unreachable: "ComfyUI 暂不可连接，节点是否已加载无法确认。",
    modelsDir: "模型目录", nodesDir: "节点目录",
    completeWorkflow: "一键补全此工作流", download: "下载", install: "安装", cancel: "取消", retry: "重试",
    resume: "继续下载", confirm: "我已了解，开始下载", back: "返回", restart: "重启 ComfyUI 以加载新节点",
    restarting: "正在重启 ComfyUI…", refresh: "刷新", ready: "已就绪", missing: "缺少", openSource: "来源",
    licence: "许可", agree: "去同意条款", node: "节点", model: "模型", toDownload: "待下载",
    state: {
      installed: "已安装", missing: "缺失", size_mismatch: "大小不符", restart_needed: "需重启 ComfyUI", unknown: "未知",
      queued: "排队中", downloading: "下载中", verifying: "校验中", installing: "安装中", done: "完成",
      failed: "失败", cancelled: "已取消",
    } as Record<string, string>,
    bundled: "安装包预装", noLicence: "无许可证", community: "社区模型", gated: "需同意条款",
    settings: "下载设置（镜像 / Token）",
  },
  en: {
    title: "Workflow dependencies",
    notice: "DS ships no third-party models or node packs. When you click download, you download the file yourself from its original publisher (Hugging Face / ModelScope / GitHub) under that publisher's licence - please read it first.",
    unlicensedWarn: "This node pack has no licence at all (the author reserves all rights). You decide whether to download and use it; DS does not provide or redistribute it.",
    gatedWarn: "This model is gated: accept its terms on Hugging Face and enter a Hugging Face token below.",
    statusOnly: "ComfyUI is not in built-in (managed) mode: status and links only - install on the machine that runs ComfyUI.",
    unreachable: "ComfyUI is not reachable right now, so loaded nodes cannot be confirmed.",
    modelsDir: "Models folder", nodesDir: "Node folder",
    completeWorkflow: "Complete this workflow", download: "Download", install: "Install", cancel: "Cancel", retry: "Retry",
    resume: "Resume", confirm: "I understand, download", back: "Back", restart: "Restart ComfyUI to load new nodes",
    restarting: "Restarting ComfyUI…", refresh: "Refresh", ready: "Ready", missing: "Missing", openSource: "Source",
    licence: "Licence", agree: "Accept terms", node: "Node pack", model: "Model", toDownload: "to download",
    state: {
      installed: "Installed", missing: "Missing", size_mismatch: "Wrong size", restart_needed: "Restart ComfyUI", unknown: "Unknown",
      queued: "Queued", downloading: "Downloading", verifying: "Verifying", installing: "Installing", done: "Done",
      failed: "Failed", cancelled: "Cancelled",
    } as Record<string, string>,
    bundled: "preinstalled", noLicence: "no licence", community: "community", gated: "gated",
    settings: "Download settings (mirror / token)",
  },
};

type Pending = { label: string; items: DepItem[]; workflow?: string };

function needsDownload(item: DepItem): boolean {
  return item.installable && (item.state === "missing" || item.state === "size_mismatch")
    && !(item.job && ACTIVE_JOB.includes(item.job.state));
}

function Progress({ item }: { item: DepItem }) {
  const job = item.job;
  if (!job || !ACTIVE_JOB.includes(job.state)) return null;
  const total = job.total || item.size || 0;
  const pct = total ? Math.min(100, Math.floor((job.done / total) * 100)) : 0;
  return (
    <div className="ds-deps-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="ds-deps-bar" style={{ width: `${total ? pct : 100}%` }} />
      <span>{total ? `${pct}% · ${formatBytes(job.done)} / ${formatBytes(total)}` : formatBytes(job.done)}</span>
    </div>
  );
}

export function DepsPanel({ active = true, model }: { active?: boolean; model?: ConfigModel | null }) {
  const { lang } = useI18n();
  const L = TEXT[lang];
  const [data, setData] = useState<DepsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [restarting, setRestarting] = useState(false);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getDeps());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const busy = Boolean(data?.jobs.some((j) => ACTIVE_JOB.includes(j.state)));
  useEffect(() => {
    if (!active || !busy) return undefined;
    timer.current = window.setInterval(() => void load(), 1000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [active, busy, load]);

  const start = async (p: Pending) => {
    setPending(null);
    try {
      if (p.workflow) await installWorkflow(p.workflow);
      else await installDeps(p.items.map((i) => ({ kind: i.kind, id: i.id })));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
    await load();
  };

  const restart = async () => {
    setRestarting(true);
    try {
      await restartComfyForDeps();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRestarting(false);
      await load();
    }
  };

  const wfTitle = (wf: DepWorkflow) => (lang === "zh" ? wf.zh || wf.en : wf.en || wf.file);

  return (
    <div className="ds-section ds-deps" aria-label={L.title}>
      <div className="ds-banner ds-deps-notice" role="note">{L.notice}</div>
      {data && !data.can_install ? <div className="ds-banner ds-warn" role="status">{L.statusOnly}</div> : null}
      {data && !data.comfy_reachable ? <div className="ds-field-help">{L.unreachable}</div> : null}
      {error ? <div className="ds-error">{error}</div> : null}
      {data ? (
        <div className="ds-kv ds-deps-dirs">
          {data.can_install ? <><span>{L.modelsDir}</span><code>{data.models_dir}</code></> : null}
          {data.can_install ? <><span>{L.nodesDir}</span><code>{data.custom_nodes_dir}</code></> : null}
          <span>Hugging Face</span><code>{data.hf_endpoint}{data.hf_token_set ? " · token ✓" : ""}</code>
        </div>
      ) : null}
      <div className="ds-row">
        <button type="button" className="btn secondary sm" onClick={() => void load()}>{L.refresh}</button>
        {data?.restart_needed && data.can_install ? (
          <button type="button" className="btn primary sm" disabled={restarting} onClick={() => void restart()}>
            {restarting ? L.restarting : L.restart}
          </button>
        ) : null}
      </div>

      {pending ? (
        <div className="ds-deps-backdrop" onClick={(e) => { if (e.target === e.currentTarget) setPending(null); }}>
        <div className="ds-deps-confirm" role="dialog" aria-modal="true" aria-label={L.confirm}>
          <strong>{pending.label}</strong>
          <p>{L.notice}</p>
          <ul>
            {pending.items.map((i) => (
              <li key={`${i.kind}:${i.id}`}>
                <code>{i.name}</code> · {formatBytes(i.size)} · {L.licence}: {i.unlicensed ? L.noLicence : (i.license || "?")}
                {i.license_url || i.repo ? <> · <a href={i.license_url || i.repo || "#"} target="_blank" rel="noreferrer">{L.openSource}</a></> : null}
              </li>
            ))}
          </ul>
          {pending.items.some((i) => i.unlicensed) ? <div className="ds-banner ds-warn">⚠ {L.unlicensedWarn}</div> : null}
          {pending.items.some((i) => i.gated) && !data?.hf_token_set ? <div className="ds-banner ds-warn">{L.gatedWarn}</div> : null}
          <div className="ds-row">
            <button type="button" className="btn primary sm" onClick={() => void start(pending)}>{L.confirm}</button>
            <button type="button" className="btn ghost sm" onClick={() => setPending(null)}>{L.back}</button>
          </div>
        </div>
        </div>
      ) : null}

      <div className="ds-deps-list">
        {data?.workflows.map((wf) => {
          // Director LLM group: GGUFs live on this machine (installable even with a remote ComfyUI)
          // and only the default (first) model is fetched, never the whole list.
          const isLlm = wf.kind === "llm";
          const canInstall = Boolean(data.can_install || isLlm);
          const todo = isLlm ? wf.items.filter(needsDownload).slice(0, 1) : wf.items.filter(needsDownload);
          return (
            <section key={wf.file} className={`ds-deps-card${wf.ready ? " ready" : ""}`} aria-label={wfTitle(wf)}>
              <header>
                <strong>{wfTitle(wf)}</strong>
                <code className="ds-deps-file">{wf.file}</code>
                <span className={`ds-badge ${wf.ready ? "ds-st-ok" : "ds-st-warn"}`}>
                  {wf.ready ? L.ready : `${L.missing} · ${formatBytes(wf.missing_bytes)} ${L.toDownload}`}
                </span>
                {canInstall && todo.length && !(isLlm && wf.ready) ? (
                  <button type="button" className="btn primary sm"
                    onClick={() => setPending(isLlm
                      ? { label: `${L.download}: ${todo[0].name}`, items: todo }
                      : { label: `${L.completeWorkflow}: ${wfTitle(wf)}`, items: todo, workflow: wf.file })}>
                    {isLlm ? L.download : L.completeWorkflow}
                  </button>
                ) : null}
              </header>
              <ul>
                {wf.items.map((item) => {
                  const shown = item.job && item.job.state !== "done" ? item.job.state : item.state;
                  const href = item.kind === "pack" ? item.repo : item.page;
                  return (
                    <li key={`${item.kind}:${item.id}`} className={`ds-deps-item ds-deps-${shown}`}>
                      <span className="ds-badge">{item.kind === "pack" ? L.node : L.model}</span>
                      <code className="ds-deps-name">{item.name}</code>
                      <span className="ds-deps-size">{formatBytes(item.size)}</span>
                      <span className={`ds-badge ds-deps-state-${shown}`}>{L.state[shown] ?? shown}</span>
                      {item.unlicensed ? <span className="ds-badge ds-badge-restart">⚠ {L.noLicence}</span>
                        : item.license ? <a className="ds-badge" href={item.license_url || item.repo || undefined} target="_blank" rel="noreferrer">{item.license}</a> : null}
                      {item.community ? <span className="ds-badge">{L.community}</span> : null}
                      {item.preinstalled || item.bundled ? <span className="ds-badge ds-deps-preinstalled">{L.bundled}</span> : null}
                      {item.gated ? <a className="ds-badge ds-badge-restart" href={item.page} target="_blank" rel="noreferrer">{L.gated} · {L.agree}</a> : null}
                      {href ? <a className="ds-deps-link" href={href} target="_blank" rel="noreferrer">{L.openSource}</a> : null}
                      {canInstall && needsDownload(item) ? (
                        <button type="button" className="btn secondary sm"
                          onClick={() => setPending({ label: item.name, items: [item] })}>
                          {item.partial ? L.resume : item.job?.state === "failed" ? L.retry : item.kind === "pack" ? L.install : L.download}
                        </button>
                      ) : null}
                      {item.job && ACTIVE_JOB.includes(item.job.state) ? (
                        <button type="button" className="btn ghost sm" onClick={() => void cancelDep(item.kind, item.id).then(load)}>{L.cancel}</button>
                      ) : null}
                      <Progress item={item} />
                      {item.job?.state === "failed" && item.job.error ? <div className="ds-error ds-deps-error">{item.job.error}</div> : null}
                      {(!item.installable || item.preinstalled || item.unlicensed || isLlm) && item.note ? <div className="ds-field-help">{(lang === "zh" ? item.note.zh || item.note.en : item.note.en) || ""}</div> : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>

      {model ? (
        <details className="ds-group">
          <summary>{L.settings}</summary>
          <GroupForm model={model} group="deps" />
        </details>
      ) : null}
    </div>
  );
}
