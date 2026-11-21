import { defineStore } from "pinia";

const KEY = "echodesk-session";

export const useSession = defineStore("session", {
  state: () => ({
    accessToken: "",
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
      this.role = payload.role;
      this.tenantId = payload.tenant_id || "";
      this.mustChangePassword = payload.must_change_password;
      this.username = payload.username || this.username;
      sessionStorage.setItem(KEY, JSON.stringify(this.$state));
    },
    clear() {
      this.accessToken = "";
      this.role = "";
      this.tenantId = "";
      this.mustChangePassword = false;
      sessionStorage.removeItem(KEY);
    },
  },
});
