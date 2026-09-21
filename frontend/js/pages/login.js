import { apiGet } from "../api.js";
import { el, clear, toast } from "../ui.js";
import { state } from "../state.js";

export async function render() {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";
  view.style.maxWidth = "none";

  const wrap = el("div", { class: "login-wrap" });
  const card = el("div", { class: "login-card" },
    el("div", { class: "brand-mark" }, "D"),
    el("h1", {}, "DriveDoc Control"),
    el("p", {}, "Cloud-based project &amp; document control for Google Drive."),
    el("button", { class: "google-btn", onclick: startFlow },
      googleIcon(), "Continue with Google"),
    el("p", { class: "muted", style: "margin-top:16px;font-size:11px" },
      "Sign in with your organization Google account. Drive access is used only for the folders you connect."),
  );
  wrap.append(card);
  view.append(wrap);
}

function googleIcon() {
  return '<svg viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>';
}

async function startFlow() {
  const btn = document.querySelector(".google-btn");
  btn.disabled = true;
  btn.textContent = "Redirecting…";
  try {
    const r = await apiGet("/api/auth/login");
    location.assign(r.url);
  } catch (e) {
    toast(e.message, "err");
    btn.disabled = false;
    btn.innerHTML = googleIcon() + "Continue with Google";
  }
}