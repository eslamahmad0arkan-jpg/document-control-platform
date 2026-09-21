const BASE = "";

export class ApiError extends Error {
  constructor(status, code, message, details = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(method, path, body) {
  const opts = { method, headers: {}, credentials: "include" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, opts);
  if (res.status === 204) return null;
  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("json")) {
    try { data = await res.json(); } catch { data = null; }
  }
  if (!res.ok) {
    const err = (data && data.error) || {};
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("app:unauthorized"));
    }
    throw new ApiError(res.status, err.code || "ERROR", err.message || res.statusText, err.details || {});
  }
  return data;
}

export function apiGet(path) { return request("GET", path); }
export function apiPost(path, body) { return request("POST", path, body); }
export function apiPatch(path, body) { return request("PATCH", path, body); }
export function apiDelete(path) { return request("DELETE", path); }

export function pushPath(path) {
  if (location.hash === "#" + path) location.reload();
  else location.hash = path;
}