/**
 * frontend/static/js/auth.js
 * ──────────────────────────
 * Vanilla-JS auth layer for TalentIQ ATS.
 * Mirrors the logic from src/context/AuthContext.jsx + src/lib/api.js
 * with zero build tooling — just include this script in any HTML page.
 *
 * Usage
 *   Protected page:  call Auth.checkAuth()  at the top of DOMContentLoaded.
 *   API calls:       use  Auth.authFetch()  instead of fetch() everywhere.
 *   Nav / logout:    Auth.getUser() / Auth.logout()
 */

const Auth = (() => {
  // ── JWT helpers ────────────────────────────────────────────────────────
  function decodeJwt(token) {
    try {
      const payload = token.split(".")[1];
      const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
      return JSON.parse(json);
    } catch {
      return null;
    }
  }

  /** Returns the stored token, or null if absent / expired. */
  function getToken() {
    const token = localStorage.getItem("access_token");
    if (!token) return null;
    const payload = decodeJwt(token);
    if (payload?.exp && payload.exp * 1000 < Date.now()) {
      _clear();
      return null;
    }
    return token;
  }

  /** Returns the stored user profile object, or null. */
  function getUser() {
    try {
      return JSON.parse(localStorage.getItem("ats_user") || "null");
    } catch {
      return null;
    }
  }

  function _clear() {
    // NOTE: Tenant-scoped job history (ats_jobs_c<id>) is intentionally
    // preserved on logout so users see their own history when they log
    // back in. Cross-tenant leaks are prevented by the scoped key itself.
    localStorage.removeItem("access_token");
    localStorage.removeItem("ats_user");
  }

  // ── Page guard ─────────────────────────────────────────────────────────
  /**
   * Call at the very top of DOMContentLoaded on any protected page.
   * Immediately redirects to /login if no valid token is found.
   */
  function checkAuth() {
    if (!getToken()) {
      window.location.replace("/login");
    }
  }

  // ── authFetch ──────────────────────────────────────────────────────────
  /**
   * Drop-in replacement for fetch() that automatically attaches the
   * Authorization: Bearer header and redirects to /login on 401.
   *
   * @param {string}       url     — relative or absolute URL
   * @param {RequestInit}  options — same options as fetch()
   * @returns {Promise<Response>}
   */
  async function authFetch(url, options = {}) {
    const token = getToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const response = await fetch(url, { ...options, headers });

    if (response.status === 401) {
      _clear();
      window.location.replace("/login");
      // Return a never-resolving promise so the caller's code doesn't
      // continue executing while the redirect is in progress.
      return new Promise(() => {});
    }

    return response;
  }

  // ── login ──────────────────────────────────────────────────────────────
  /**
   * Authenticates with the FastAPI backend using OAuth2 password flow.
   * FastAPI's OAuth2PasswordRequestForm expects application/x-www-form-urlencoded
   * with a field named `username` (we pass the email there).
   *
   * @param {string} email
   * @param {string} password
   * @returns {Promise<{id, company_id, email}>} resolved user profile
   * @throws  {Error} with a human-readable message on failure
   */
  async function login(email, password) {
    const body = new URLSearchParams();
    body.append("username", email); // FastAPI OAuth2 expects "username"
    body.append("password", password);

    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _throwApiError(err);
    }

    const { access_token } = await res.json();
    const payload = decodeJwt(access_token);

    const profile = {
      id:         payload?.user_id    ?? null,
      company_id: payload?.company_id ?? null,
      email,
    };

    localStorage.setItem("access_token", access_token);
    localStorage.setItem("ats_user", JSON.stringify(profile));

    return profile;
  }

  // ── register ───────────────────────────────────────────────────────────
  /**
   * Creates a new Company + admin User, then immediately logs in.
   *
   * @param {string} email
   * @param {string} password
   * @param {string} company_name
   * @returns {Promise<{id, company_id, email}>}
   */
  async function register(email, password, company_name) {
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, company_name }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _throwApiError(err);
    }

    // Auto-login so the caller gets a token immediately
    return login(email, password);
  }

  // ── logout ─────────────────────────────────────────────────────────────
  function logout() {
    _clear();
    window.location.replace("/login");
  }

  // ── internal error helper ──────────────────────────────────────────────
  function _throwApiError(errBody) {
    const detail = errBody?.detail;
    if (typeof detail === "string")  throw new Error(detail);
    if (Array.isArray(detail))       throw new Error(detail.map(d => d.msg).join(", "));
    throw new Error("An unexpected error occurred. Please try again.");
  }

  // ── Public API ─────────────────────────────────────────────────────────
  return { checkAuth, authFetch, login, register, logout, getUser, getToken };
})();
