/**
 * Extension-wide runtime constants. Hoisted out of content.ts so the proxy
 * base URL has one source of truth (was duplicated 6× across content.ts).
 *
 * The proxy is the Python sidecar at scripts/run-proxy.sh; it owns the
 * Rust engine on :7270 and exposes the streaming + auxiliary endpoints
 * the extension consumes.
 */

export const PROXY_BASE_URL = 'http://localhost:7271';

/** Streaming engine analysis endpoint — primary POST target on every turn. */
export const ENGINE_STREAM_URL = `${PROXY_BASE_URL}/analyze/stream`;

/** Free-form play-note POST endpoint. Mirrors localStorage to disk under
 *  analysis/play-notes/YYYY-MM-DD.jsonl for offline review. */
export const PROXY_ANNOTATION_URL = `${PROXY_BASE_URL}/annotation`;

/** Battle post-mortem POST endpoint. Battle-id keyed; later POSTs overwrite
 *  earlier ones so the disk archive converges to one file per battle. */
export const PROXY_POSTMORTEM_URL = `${PROXY_BASE_URL}/postmortem`;

/** Poll interval for the content-script's battle-state watcher. */
export const POLL_MS = 500;

/** Engine analysis time budget per request (ms). Tuned for MCTS quality vs
 *  responsiveness on a typical NatDex turn. */
export const ANALYSIS_TIME_MS = 6000;

/** Streaming update interval (ms) — how often the engine flushes interim
 *  results during the analysis window. */
export const UPDATE_INTERVAL_MS = 400;
