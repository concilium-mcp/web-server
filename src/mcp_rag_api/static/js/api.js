/* Cliente HTTP da dashboard — todas as chamadas vão para /dash/api. */
import { state } from "./state.js";
import { t } from "./i18n.js";
import { emit } from "./events.js";

const api = {
  async req(method, path, body) {
    const r = await fetch(`/dash/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401 && state.user) {
      // sessão caiu (expirou/revogada): volta para o login (o handler é registrado no main)
      emit("session:end");
      throw new Error(t("common.sessionExpired"));
    }
    if (!r.ok) throw Object.assign(new Error(`${r.status}`), { status: r.status, detail: (await r.json().catch(() => ({}))).detail });
    return r.status === 204 ? null : r.json();
  },
  get(path, params) {
    const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "")) : "";
    return this.req("GET", `${path}${qs}`);
  },
  post(path, body) {
    return this.req("POST", path, body ?? {});
  },
  patch(path, body) {
    return this.req("PATCH", path, body ?? {});
  },
  del(path) {
    return this.req("DELETE", path);
  },
};

export { api };
