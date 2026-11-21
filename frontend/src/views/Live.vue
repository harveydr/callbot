<template>
  <div>
    <h1>实时通话</h1>
    <el-alert v-if="notice" :title="notice" type="warning" show-icon />
    <p>连接状态：{{ state }}</p>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api/client";

const state = ref("connecting");
const notice = ref("");
let socket;
let stopped = false;

async function connect() {
  if (stopped) return;
  state.value = "connecting";
  try {
    const response = await api.get("/api/t/ws/tickets");
    const ticket = response.data.data.ticket;
    const base = (import.meta.env.VITE_BASE_URL || "http://localhost:8000").replace(/^http/, "ws");
    socket = new WebSocket(`${base}/api/t/live/ws?ticket=${ticket}`);
    socket.onopen = () => {
      state.value = "open";
      notice.value = "";
    };
    socket.onclose = () => {
      state.value = "closed";
      notice.value = "实时连接已断开，正在重连";
      window.setTimeout(connect, 2000);
    };
  } catch {
    state.value = "error";
    notice.value = "实时连接已断开，正在重连";
    window.setTimeout(connect, 2000);
  }
}

onMounted(connect);
onBeforeUnmount(() => {
  stopped = true;
  if (socket) socket.close();
});
</script>
