<template>
  <div>
    <PageHeader
      title="实时通话"
      description="WebSocket 仅维持心跳，不推送转写。下方为轮询的在途通话看板（能力受 API 字段限制）。"
    />
    <el-alert v-if="notice" :title="notice" type="warning" show-icon style="margin-bottom: 12px" />
    <div class="ed-card">
      <p>连接状态：<el-tag :type="stateType">{{ stateLabel }}</el-tag></p>
      <p class="ed-muted">监听、强插、实时转写需要后端事件推送，当前版本未实现。</p>
      <el-button @click="refreshCalls">刷新在途列表</el-button>
    </div>
    <StateView :status="callsStatus" :message="callsMessage" empty-text="当前没有在途通话" @retry="refreshCalls">
      <el-table :data="calls">
        <el-table-column prop="id" label="通话 ID" min-width="220" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="callStatusLabels" /></template>
        </el-table-column>
        <el-table-column prop="block_reason" label="拦截原因" />
        <el-table-column prop="template_version" label="话术版本" width="100" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api/client";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { callStatusLabels } from "../utils/enums";

const state = ref("connecting");
const notice = ref("");
const calls = ref([]);
const callsStatus = ref("empty");
const callsMessage = ref("");
let socket;
let stopped = false;
let pollTimer = 0;

const inProgress = new Set(["queued", "dialing", "ringing", "answered", "transferring"]);

const stateLabel = computed(() => {
  if (state.value === "open") return "已连接";
  if (state.value === "connecting") return "连接中";
  if (state.value === "closed") return "已断开，重连中";
  return "连接异常";
});

const stateType = computed(() => {
  if (state.value === "open") return "success";
  if (state.value === "connecting") return "info";
  return "warning";
});

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
      notice.value = "实时连接已断开，正在重连…";
      window.setTimeout(connect, 2000);
    };
    socket.onerror = () => {
      state.value = "error";
    };
  } catch {
    state.value = "error";
    notice.value = "无法获取连接票据，将重试";
    window.setTimeout(connect, 2000);
  }
}

async function refreshCalls() {
  callsStatus.value = "loading";
  try {
    const response = await api.get("/api/t/calls", { params: { page: 1, page_size: 50 } });
    const all = response.data.data.items || [];
    calls.value = all.filter((row) => inProgress.has(row.status));
    callsStatus.value = calls.value.length ? "ready" : "empty";
  } catch (err) {
    callsStatus.value = "error";
    callsMessage.value = err.response?.data?.message || "加载失败";
  }
}

onMounted(() => {
  connect();
  refreshCalls();
  pollTimer = window.setInterval(refreshCalls, 5000);
});

onBeforeUnmount(() => {
  stopped = true;
  window.clearInterval(pollTimer);
  if (socket) socket.close();
});
</script>