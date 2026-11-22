import { defineStore } from "pinia";
import { roleHome } from "../utils/navigation";

const KEY = "echodesk-session";

export const useSession = defineStore("session", {
  state: () => ({
    accessToken: "",
    refreshToken: "",
    expiresIn: 0,
    role: "",
    tenantId: "",
    mustChangePassword: false,
    username: "",
  }),
  actions: {
    hydrate() {
      const raw = sessionStorage.getItem(KEY);
      if (!raw) return;
      Object.assign(this, JSON.parse(raw));
    },
    save(payload) {
      this.accessToken = payload.access_token;
      this.refreshToken = payload.refresh_token || this.refreshToken;
      this.expiresIn = payload.expires_in || this.expiresIn;
      this.role = payload.role;
      this.tenantId = payload.tenant_id || "";
      this.mustChangePassword = payload.must_change_password;
      if (payload.username) this.username = payload.username;
      sessionStorage.setItem(KEY, JSON.stringify(this.$state));
    },
    clear() {
      this.accessToken = "";
      this.refreshToken = "";
      this.expiresIn = 0;
      this.role = "";
      this.tenantId = "";
      this.mustChangePassword = false;
      this.username = "";
      sessionStorage.removeItem(KEY);
    },
    homePath() {
      return roleHome(this.role);
    },
  },
});