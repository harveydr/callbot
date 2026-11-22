<template>
  <div>
    <PageHeader title="报表概览" description="指标卡片与明细导出（导出需二次确认）。">
      <template #actions>
        <el-button v-if="canExport" type="primary" @click="exportReport">导出通话明细</el-button>
      </template>
    </PageHeader>
    <el-alert v-if="exportNote" :title="exportNote" type="success" show-icon :closable="false" style="margin-bottom: 12px" />
    <StateView :status="status" :message="message" @retry="load">
      <div class="ed-metric-grid">
        <div v-for="m in metrics" :key="m.key" class="ed-metric">
          <span>{{ m.label }}</span>
          <strong>{{ format(m.key, data[m.key]) }}</strong>
        </div>
      </div>
      <el-alert type="info" :closable="false" title="报表 API 不支持按时间/任务筛选，展示全量 overview。" style="margin-top: 16px" />
    </StateView>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessageBox } from "element-plus";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import { canExportReports } from "../utils/permissions";

const session = useSession();
const canExport = computed(() => canExportReports(session.role));
const status = ref("loading");
const message = ref("");
const exportNote = ref("");
const data = ref({});

const metrics = [
  { key: "calls", label: "呼叫" },
  { key: "completed", label: "完成" },
  { key: "blocked_compliance", label: "合规拦截" },
  { key: "answer_rate", label: "接通率" },
  { key: "transfer_rate", label: "转人工率" },
  { key: "no_consent_blocks", label: "无授权拦截" },
  { key: "dnd_blocks", label: "DND 拦截" },
  { key: "top_compliance_rule", label: "最高命中规则" },
];

function format(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (key.endsWith("_rate")) return `${(Number(value) * 100).toFixed(1)}%`;
  return value;
}

async function load() {
  status.value = "loading";
  try {
    const response = await api.get("/api/t/reports/overview");
    data.value = response.data.data;
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

async function exportReport() {
  try {
    await ElMessageBox.confirm("导出包含通话明细，下载链接 15 分钟内有效。确认导出？", "导出确认");
  } catch {
    return;
  }
  const response = await api.get("/api/t/reports/export");
  let payload = response.data.data;
  for (let attempt = 0; attempt < 10 && payload.status === "running"; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const polled = await api.get("/api/t/reports/export", { params: { task_id: payload.task_id } });
    payload = polled.data.data;
  }
  if (payload.status === "done" && payload.download_token) {
    const file = await api.get("/api/t/reports/export", {
      params: { task_id: payload.task_id, download: 1, token: payload.download_token },
      responseType: "blob",
    });
    const url = URL.createObjectURL(file.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `report-${payload.task_id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    exportNote.value = `报表已下载，任务 ${payload.task_id}`;
    return;
  }
  exportNote.value = `导出任务 ${payload.task_id} 仍在生成`;
}

load();
</script>