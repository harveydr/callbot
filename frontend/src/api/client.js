import axios from "axios";

const baseURL = import.meta.env.VITE_BASE_URL || "http://localhost:8000";

export const api = axios.create({ baseURL, timeout: 3000 });

api.interceptors.request.use((config) => {
  const raw = sessionStorage.getItem("echodesk-session");
  if (raw) {
    const session = JSON.parse(raw);
    if (session.accessToken) {
      config.headers.Authorization = `Bearer ${session.accessToken}`;
    }
  }
  return config;
});
