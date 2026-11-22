import axios from "axios";
import { useSession } from "../stores/session";

const baseURL = import.meta.env.VITE_BASE_URL || "http://localhost:8000";

export const api = axios.create({ baseURL, timeout: 8000 });

let refreshPromise = null;

function readSession() {
  const raw = sessionStorage.getItem("echodesk-session");
  return raw ? JSON.parse(raw) : null;
}

function writeToken(accessToken) {
  const session = readSession();
  if (!session) return;
  session.accessToken = accessToken;
  sessionStorage.setItem("echodesk-session", JSON.stringify(session));
  const store = useSession();
  store.accessToken = accessToken;
}

api.interceptors.request.use((config) => {
  const session = readSession();
  if (session?.accessToken) {
    config.headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (!original || original._retried) return Promise.reject(error);
    if (error.response?.status !== 401) return Promise.reject(error);
    const session = readSession();
    if (!session?.refreshToken || original.url?.includes("/api/auth/refresh")) {
      return Promise.reject(error);
    }
    try {
      if (!refreshPromise) {
        refreshPromise = axios
          .post(`${baseURL}/api/auth/refresh`, { refresh_token: session.refreshToken })
          .then((res) => {
            const data = res.data.data;
            const store = useSession();
            store.save({ ...data, username: session.username });
            return data.access_token;
          })
          .finally(() => {
            refreshPromise = null;
          });
      }
      const token = await refreshPromise;
      writeToken(token);
      original._retried = true;
      original.headers.Authorization = `Bearer ${token}`;
      return api(original);
    } catch {
      const store = useSession();
      store.clear();
      return Promise.reject(error);
    }
  },
);