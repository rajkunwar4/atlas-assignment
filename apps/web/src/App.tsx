import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Download,
  FileClock,
  FileUp,
  History,
  LayoutDashboard,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Unlink2,
  X,
} from "lucide-react";
import { ApiClient } from "./api";
import type {
  Audit,
  Health,
  Ingestion,
  ResultItem,
  Results,
  Run,
  Source,
} from "./types";

const PYTHON_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const NODE_URL = "http://localhost:8001";
const statuses = [
  "",
  "MATCHED",
  "DIFFERENT",
  "UNMATCHED_LEDGER",
  "UNMATCHED_COUNTERPARTY",
  "MANUALLY_MATCHED",
  "ACCEPTED_UNMATCHED",
  "EXCLUDED_CANCELLED",
];
const label = (value: string) =>
  value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
const when = (value: string) =>
  new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

function StatusPill({ status }: { status: string }) {
  const tone = status.includes("UNMATCHED")
    ? "warning"
    : status === "DIFFERENT"
      ? "danger"
      : status.includes("MATCHED")
        ? "success"
        : "muted";
  return (
    <span className={`pill ${tone}`}>
      <span />
      {label(status)}
    </span>
  );
}
function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Unlink2 size={23} />
      </div>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}

export default function App() {
  const [backend, setBackend] = useState(PYTHON_URL);
  const api = useMemo(() => new ApiClient(backend), [backend]);
  const [health, setHealth] = useState<Health | null>(null);
  const [files, setFiles] = useState<Ingestion[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeRun, setActiveRun] = useState<number | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [tab, setTab] = useState("overview");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<ResultItem | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [settings, setSettings] = useState<Record<string, string | number>>({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ kind: string; text: string } | null>(
    null,
  );
  const load = useCallback(async () => {
    try {
      const [h, f, r, a, s] = await Promise.all([
        api.health(),
        api.files(),
        api.runs(),
        api.audit(),
        api.settings(),
      ]);
      setHealth(h);
      setFiles(f);
      setRuns(r);
      setAudit(a);
      setSettings(s);
      setActiveRun((current) => current ?? r[0]?.id ?? null);
      setNotice(null);
    } catch (e: any) {
      setHealth(null);
      setNotice({
        kind: "error",
        text: `Cannot reach ${backend}. ${e.message}`,
      });
    }
  }, [api, backend]);
  useEffect(() => {
    setActiveRun(null);
    setResults(null);
    load();
  }, [load]);
  useEffect(() => {
    if (!activeRun) {
      setResults(null);
      return;
    }
    api
      .results(activeRun, status, search)
      .then(setResults)
      .catch((e: any) => setNotice({ kind: "error", text: e.message }));
  }, [api, activeRun, status, search]);
  const act = async (name: string, fn: () => Promise<any>, success: string) => {
    setBusy(name);
    try {
      await fn();
      await load();
      if (activeRun) {
        const latest = (await api.runs())[0];
        setActiveRun(latest?.id ?? activeRun);
        setResults(await api.results(latest?.id ?? activeRun, status, search));
      }
      setNotice({ kind: "success", text: success });
    } catch (e: any) {
      setNotice({ kind: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };
  const upload = async (source: Source, file?: File) => {
    if (!file) return;
    await act(
      `upload-${source}`,
      () => api.upload(source, file),
      `${file.name} was validated and ingested.`,
    );
  };
  const startRun = () =>
    act(
      "run",
      async () => {
        const run = await api.createRun();
        setActiveRun(run.id);
        setTab("results");
      },
      "Reconciliation completed with a reproducible snapshot.",
    );
  const latest = (source: Source) => files.find((f) => f.source === source);
  const currentRun = runs.find((r) => r.id === activeRun) ?? runs[0];
  const nav = [
    { id: "overview", icon: LayoutDashboard, text: "Overview" },
    { id: "results", icon: SlidersHorizontal, text: "Reconciliation" },
    { id: "activity", icon: History, text: "Activity" },
    { id: "settings", icon: Settings, text: "Settings" },
  ];
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="mark">L</div>
          <div>
            <strong>Ledgerline</strong>
            <small>RECONCILIATION</small>
          </div>
        </div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              className={tab === n.id ? "active" : ""}
              onClick={() => setTab(n.id)}
            >
              <n.icon size={18} />
              {n.text}
              {n.id === "results" && currentRun ? (
                <span className="nav-count">
                  {currentRun.summary.DIFFERENT ?? 0}
                </span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="operator">
            <span>RO</span>
            <div>
              <strong>Raj Operator</strong>
              <small>demo.operator</small>
            </div>
          </div>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">OPERATIONS / DAILY CLOSE</p>
            <h1>{tab === "results" ? "Reconciliation" : label(tab)}</h1>
          </div>
          <div className="header-actions">
            <label className="backend">
              <span className={health ? "online" : "offline"} />
              <select
                aria-label="Backend implementation"
                value={backend}
                onChange={(e) => setBackend(e.target.value)}
              >
                <option value={PYTHON_URL}>Python · FastAPI</option>
                <option value={NODE_URL}>Node · Fastify</option>
              </select>
            </label>
            <button
              className="icon-button"
              aria-label="Refresh data"
              onClick={load}
            >
              <RefreshCw size={17} />
            </button>
            <button
              className="primary"
              disabled={busy === "run"}
              onClick={startRun}
            >
              {busy === "run" ? (
                <RefreshCw className="spin" size={17} />
              ) : (
                <Activity size={17} />
              )}
              Start run
            </button>
          </div>
        </header>
        {notice ? (
          <div className={`notice ${notice.kind}`}>
            {notice.kind === "success" ? (
              <Check size={17} />
            ) : (
              <TriangleAlert size={17} />
            )}
            <span>{notice.text}</span>
            <button aria-label="Dismiss" onClick={() => setNotice(null)}>
              <X size={15} />
            </button>
          </div>
        ) : null}
        <div className="content">
          {tab === "overview" && (
            <Overview
              files={files}
              runs={runs}
              latest={latest}
              upload={upload}
              busy={busy}
              onOpenRun={(id) => {
                setActiveRun(id);
                setTab("results");
              }}
            />
          )}
          {tab === "results" && (
            <ResultsView
              run={currentRun}
              results={results}
              status={status}
              setStatus={setStatus}
              search={search}
              setSearch={setSearch}
              select={setSelected}
              backend={backend}
            />
          )}{" "}
          {tab === "activity" && <ActivityView audit={audit} />}{" "}
          {tab === "settings" && (
            <SettingsView
              settings={settings}
              setSettings={setSettings}
              save={() =>
                act(
                  "settings",
                  () => api.updateSettings(settings),
                  "Tolerance settings were updated for future runs.",
                )
              }
              busy={busy}
            />
          )}
        </div>
      </main>
      {selected ? (
        <Detail
          item={selected}
          all={results?.items ?? []}
          close={() => setSelected(null)}
          match={(l, c, n) =>
            act(
              "resolve",
              () => api.match(l, c, n),
              "Manual match saved and applied.",
            ).then(() => setSelected(null))
          }
          accept={(id, n) =>
            act(
              "resolve",
              () => api.accept(id, n),
              "The transaction was accepted as genuinely unmatched.",
            ).then(() => setSelected(null))
          }
        />
      ) : null}
    </div>
  );
}

function Overview({
  files,
  runs,
  latest,
  upload,
  busy,
  onOpenRun,
}: {
  files: Ingestion[];
  runs: Run[];
  latest: (s: Source) => Ingestion | undefined;
  upload: (s: Source, f?: File) => void;
  busy: string;
  onOpenRun: (id: number) => void;
}) {
  return (
    <>
      <section className="hero">
        <div>
          <span className="kicker">
            <ShieldCheck size={15} /> Controlled daily close
          </span>
          <h2>
            Find the differences.
            <br />
            <em>Keep the decisions.</em>
          </h2>
          <p>
            Upload both books, run a deterministic comparison, and resolve only
            the exceptions that require human judgment.
          </p>
        </div>
        <div className="hero-stat">
          <small>LATEST RUN</small>
          {runs[0] ? (
            <>
              <strong>#{String(runs[0].id).padStart(3, "0")}</strong>
              <span>
                <span className="live-dot" />
                Completed {when(runs[0].created_at)}
              </span>
            </>
          ) : (
            <>
              <strong>—</strong>
              <span>No reconciliation yet</span>
            </>
          )}
        </div>
      </section>
      <section>
        <div className="section-title">
          <div>
            <p className="eyebrow">01 / INPUTS</p>
            <h2>Source files</h2>
          </div>
          <p>
            CSV files are validated atomically. Corrections create immutable row
            versions.
          </p>
        </div>
        <div className="source-grid">
          {(["LEDGER", "COUNTERPARTY"] as Source[]).map((source, i) => (
            <div className="source-card" key={source}>
              <div className="source-top">
                <div className={`source-icon s${i}`}>
                  <Database size={21} />
                </div>
                <div>
                  <small>{i ? "EXTERNAL SOURCE" : "INTERNAL SOURCE"}</small>
                  <h3>{i ? "Counterparty statement" : "Your ledger"}</h3>
                </div>
                <span className={latest(source) ? "ready" : "waiting"}>
                  {latest(source) ? "Ready" : "Needed"}
                </span>
              </div>
              {latest(source) ? (
                <div className="file-row">
                  <div>
                    <FileClock size={18} />
                    <span>
                      <strong>{latest(source)!.filename}</strong>
                      <small>
                        {latest(source)!.row_count} rows ·{" "}
                        {latest(source)!.changed_count} changed
                      </small>
                    </span>
                  </div>
                  <time>{when(latest(source)!.created_at)}</time>
                </div>
              ) : (
                <div className="file-row placeholder">
                  <span>No accepted file yet</span>
                </div>
              )}
              <label className="upload-button">
                <FileUp size={17} />
                {busy === `upload-${source}` ? "Validating…" : "Choose CSV"}
                <input
                  type="file"
                  accept=".csv,text/csv"
                  disabled={busy !== ""}
                  onChange={(e) => upload(source, e.target.files?.[0])}
                />
              </label>
            </div>
          ))}
        </div>
      </section>
      <section>
        <div className="section-title">
          <div>
            <p className="eyebrow">02 / HISTORY</p>
            <h2>Recent runs</h2>
          </div>
        </div>
        <div className="run-list">
          {runs.length ? (
            runs.slice(0, 5).map((run) => (
              <button key={run.id} onClick={() => onOpenRun(run.id)}>
                <span className="run-id">
                  #{String(run.id).padStart(3, "0")}
                </span>
                <span>
                  <strong>{run.summary.DIFFERENT ?? 0} differences</strong>
                  <small>
                    {(run.summary.UNMATCHED_LEDGER ?? 0) +
                      (run.summary.UNMATCHED_COUNTERPARTY ?? 0)}{" "}
                    unmatched · {run.summary.MATCHED ?? 0} clean
                  </small>
                </span>
                <time>{when(run.created_at)}</time>
                <ChevronRight size={18} />
              </button>
            ))
          ) : (
            <Empty
              title="No runs yet"
              body="Upload both source files, then start your first reconciliation."
            />
          )}
        </div>
      </section>
    </>
  );
}

function ResultsView({
  run,
  results,
  status,
  setStatus,
  search,
  setSearch,
  select,
  backend,
}: {
  run?: Run;
  results: Results | null;
  status: string;
  setStatus: (s: string) => void;
  search: string;
  setSearch: (s: string) => void;
  select: (x: ResultItem) => void;
  backend: string;
}) {
  if (!run)
    return (
      <Empty
        title="Nothing to reconcile yet"
        body="Upload one file for each source and start a run."
      />
    );
  const cards = [
    { key: "MATCHED", label: "Clean matches", icon: Check },
    { key: "DIFFERENT", label: "With differences", icon: TriangleAlert },
    { key: "UNMATCHED", label: "Unmatched", icon: Unlink2 },
    { key: "EXCLUDED_CANCELLED", label: "Excluded", icon: X },
  ];
  return (
    <>
      <div className="run-heading">
        <div>
          <p className="eyebrow">RUN #{String(run.id).padStart(3, "0")}</p>
          <h2>Exception workspace</h2>
          <p>
            Snapshot created {when(run.created_at)} · served by{" "}
            {backend.includes("8001") ? "Node" : "Python"}
          </p>
        </div>
        <a className="secondary" href={`${backend}/api/runs/${run.id}/export`}>
          <Download size={16} />
          Export CSV
        </a>
      </div>
      <div className="metric-grid">
        {cards.map((c) => (
          <div className={`metric ${c.key.toLowerCase()}`} key={c.key}>
            <c.icon size={18} />
            <span>
              <strong>
                {c.key === "UNMATCHED"
                  ? (run.summary.UNMATCHED_LEDGER ?? 0) +
                    (run.summary.UNMATCHED_COUNTERPARTY ?? 0)
                  : (run.summary[c.key] ?? 0)}
              </strong>
              <small>{c.label}</small>
            </span>
          </div>
        ))}
      </div>
      <div className="toolbar">
        <label>
          <Search size={17} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search reference or instrument"
          />
        </label>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filter status"
        >
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s ? label(s) : "All statuses"}
            </option>
          ))}
        </select>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Ledger reference</th>
              <th>Counterparty</th>
              <th>Instrument</th>
              <th>Gross delta</th>
              <th>Match basis</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {results?.items.map((item) => {
              const gross = item.differences.find(
                (d) => d.field === "gross_amount",
              );
              return (
                <tr
                  key={item.id}
                  onClick={() => select(item)}
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && select(item)}
                >
                  <td>
                    <StatusPill status={item.status} />
                  </td>
                  <td>
                    <strong>{item.ledger?.external_id ?? "—"}</strong>
                    <small>
                      {item.ledger
                        ? when(item.ledger.executed_at)
                        : "No ledger row"}
                    </small>
                  </td>
                  <td>
                    <strong>{item.counterparty?.external_id ?? "—"}</strong>
                    <small>
                      {item.counterparty
                        ? when(item.counterparty.executed_at)
                        : "No external row"}
                    </small>
                  </td>
                  <td>
                    {item.ledger?.instrument ?? item.counterparty?.instrument}
                  </td>
                  <td className={gross && !gross.passed ? "negative" : ""}>
                    {gross?.absolute_delta ? `$${gross.absolute_delta}` : "—"}
                  </td>
                  <td>{label(item.match_method)}</td>
                  <td>
                    <ArrowRight size={17} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!results?.items.length ? (
          <Empty
            title="No results in this view"
            body="Try clearing the search or choosing another status."
          />
        ) : null}
      </div>
    </>
  );
}

function Detail({
  item,
  all,
  close,
  match,
  accept,
}: {
  item: ResultItem;
  all: ResultItem[];
  close: () => void;
  match: (l: number, c: number, n: string) => void;
  accept: (id: number, n: string) => void;
}) {
  const [candidate, setCandidate] = useState("");
  const [note, setNote] = useState("");
  const unmatchedLedger = item.status === "UNMATCHED_LEDGER",
    unmatchedCounter = item.status === "UNMATCHED_COUNTERPARTY",
    candidates = all.filter((x) =>
      unmatchedLedger
        ? x.status === "UNMATCHED_COUNTERPARTY"
        : x.status === "UNMATCHED_LEDGER",
    );
  const own = item.ledger ?? item.counterparty;
  return (
    <div
      className="scrim"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <aside className="drawer">
        <div className="drawer-head">
          <div>
            <StatusPill status={item.status} />
            <h2>
              {item.ledger?.external_id ?? item.counterparty?.external_id}
            </h2>
            <p>
              {item.ledger?.instrument ?? item.counterparty?.instrument} ·{" "}
              {label(item.match_method)}
            </p>
          </div>
          <button
            className="icon-button"
            aria-label="Close detail"
            onClick={close}
          >
            <X size={19} />
          </button>
        </div>
        {item.ledger && item.counterparty ? (
          <>
            <div className="compare-head">
              <span>YOUR LEDGER</span>
              <span>COUNTERPARTY</span>
            </div>
            <div className="diff-list">
              {item.differences.map((d) => (
                <div className={!d.passed ? "failed" : ""} key={d.field}>
                  <label>
                    {label(d.field)}
                    {d.passed ? (
                      <Check size={14} />
                    ) : (
                      <TriangleAlert size={14} />
                    )}
                  </label>
                  <span>{d.left}</span>
                  <ArrowRight size={14} />
                  <span>{d.right}</span>
                  {!d.passed ? (
                    <small>
                      Delta {d.absolute_delta ?? "changed"} · tolerance{" "}
                      {d.tolerance}
                    </small>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="single-record">
            <p className="eyebrow">NORMALIZED RECORD</p>
            {Object.entries(own ?? {})
              .filter(
                ([k]) => !["raw", "id", "version_id", "source"].includes(k),
              )
              .map(([k, v]) => (
                <div key={k}>
                  <span>{label(k)}</span>
                  <strong>{String(v)}</strong>
                </div>
              ))}
          </div>
        )}
        {(unmatchedLedger || unmatchedCounter) && (
          <div className="resolution">
            <h3>Resolve this exception</h3>
            <p>
              Manual decisions are bound to stable identities and will be
              applied to future corrected versions.
            </p>
            {candidates.length ? (
              <>
                <label>
                  Match with
                  <select
                    value={candidate}
                    onChange={(e) => setCandidate(e.target.value)}
                  >
                    <option value="">Select a candidate</option>
                    {candidates.map((c) => (
                      <option
                        key={c.id}
                        value={(c.ledger ?? c.counterparty)!.id}
                      >
                        {(c.ledger ?? c.counterparty)!.external_id} ·{" "}
                        {(c.ledger ?? c.counterparty)!.instrument}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            ) : (
              <p className="hint">
                No opposite-side unmatched rows are visible in the current
                filter.
              </p>
            )}
            <label>
              Resolution note
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Why is this decision appropriate?"
              />
            </label>
            <div>
              <button
                className="secondary"
                onClick={() => own && accept(own.id, note)}
              >
                Accept unmatched
              </button>
              <button
                className="primary"
                disabled={!candidate}
                onClick={() => {
                  const other = Number(candidate);
                  match(
                    unmatchedLedger ? own!.id : other,
                    unmatchedLedger ? other : own!.id,
                    note,
                  );
                }}
              >
                Save manual match
              </button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function ActivityView({ audit }: { audit: Audit[] }) {
  return (
    <section>
      <div className="section-title">
        <div>
          <p className="eyebrow">IMMUTABLE RECORD</p>
          <h2>Audit activity</h2>
        </div>
        <p>The latest 100 file, run, resolution, and configuration events.</p>
      </div>
      <div className="timeline">
        {audit.map((event) => (
          <div key={event.id}>
            <span className="timeline-icon">
              <Clock3 size={16} />
            </span>
            <section>
              <strong>{label(event.action)}</strong>
              <p>
                {event.entity_type} #{event.entity_id} · {event.actor}
              </p>
              <small>{when(event.created_at)}</small>
            </section>
          </div>
        ))}
        {!audit.length ? (
          <Empty
            title="No activity yet"
            body="Events appear after uploads, runs, and manual decisions."
          />
        ) : null}
      </div>
    </section>
  );
}

function SettingsView({
  settings,
  setSettings,
  save,
  busy,
}: {
  settings: Record<string, string | number>;
  setSettings: (s: Record<string, string | number>) => void;
  save: () => void;
  busy: string;
}) {
  return (
    <section className="settings-page">
      <div className="section-title">
        <div>
          <p className="eyebrow">CONTROLLED CONFIGURATION</p>
          <h2>Matching tolerances</h2>
        </div>
        <p>
          Changes apply only to future runs. Every run snapshots its settings.
        </p>
      </div>
      <div className="settings-card">
        {Object.entries(settings).map(([key, value]) => (
          <label key={key}>
            <span>
              <strong>{label(key)}</strong>
              <small>
                {key.includes("candidate")
                  ? "Candidate gate or score threshold"
                  : "Material comparison tolerance"}
              </small>
            </span>
            <input
              value={value}
              type="number"
              step="any"
              onChange={(e) =>
                setSettings({
                  ...settings,
                  [key]: key.endsWith("seconds")
                    ? Number(e.target.value)
                    : e.target.value,
                })
              }
            />
          </label>
        ))}
        <button
          className="primary"
          onClick={save}
          disabled={busy === "settings"}
        >
          Save settings
        </button>
      </div>
    </section>
  );
}
