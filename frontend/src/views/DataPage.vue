<template>
  <div>
    <h1>{{ title }}</h1>
    <el-button v-if="canExport" type="primary" @click="exportReport">导出明细</el-button>
    <el-alert v-if="exportNote" :title="exportNote" type="success" show-icon :closable="false" style="margin: 12px 0" />
    <StateView :status="status" :message="message" :stale="stale" @retry="load(0)">
      <div v-if="overview && items[0]" class="metrics">
        <div v-for="column in columns" :key="column" class="metric">
          <span>{{ labelOf(column) }}</span>
          <strong :class="{ formula: column === 'formula' }">{{ formatCell(column, items[0][column]) }}</strong>
        </div>
      </div>
      <el-table v-else :data="items" style="width: 100%">
        <el-table-column v-for="column in columns" :key="column" :prop="column" :label="labelOf(column)" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessageBox } from "element-plus";
import StateView from "../components/StateView.vue";
import { api } from "../api/client";
import { useSession } from "../stores/session";

const route = useRoute();
const session = useSession();
const status = ref("loading");
const message = ref("");
const exportNote = ref("");
const stale = ref("");
const items = ref([]);
const title = computed(() => route.meta.title || "列表");
const canExport = computed(() => route.path === "/t/reports" && session.role === "tenant_admin");
const overview = computed(() => route.path === "/t/reports" || route.path === "/t/billing");
const columns = computed(() => Object.keys(items.value[0] || {}));
const labels = {
  calls: "呼叫",
  completed: "完成",
  blocked_compliance: "合规拦截",
  demo_excluded: "已排除演示",
  answer_rate: "接通率",
  avg_duration_sec: "平均时长（秒）",
  intent_a: "意向 A",
  intent_b: "意向 B",
  intent_c: "意向 C",
  intent_d: "意向 D",
  intent_na: "意向 NA",
  transfer_rate: "转人工率",
  reject_rate: "拒接率",
  ai_complete_rate: "AI 独立完成率",
  top_compliance_rule: "合规命中最高规则",
  no_consent_blocks: "无授权拦截",
  dnd_blocks: "DND 拦截",
  unquoted_items: "未报价明细",
  degraded: "呼叫降级",
  item_count: "明细笔数",
  amount: "金额",
  currency: "币种",
  rate_status: "报价状态",
  formula: "计费公式",
  billing_budget: "月度上限",
  month: "账期",
  month_quoted: "本月已报价",
  month_unquoted: "本月未报价笔数",
  new_tasks_paused: "新任务已暂停",
};

function labelOf(column) {
  return labels[column] || column;
}

function formatCell(column, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (column.endsWith("_rate")) return `${(Number(value) * 100).toFixed(1)}%`;
  if (typeof value === "boolean") return value ? "是" : "否";
  return value;
}

async function exportReport() {
  try {
    await ElMessageBox.confirm("导出包含通话明细，链接 15 分钟内有效。确认导出？", "导出确认");
  } catch {
    return;
  }
  const response = await api.get("/api/t/reports/export");
  let data = response.data.data;
  for (let attempt = 0; attempt < 10 && data.status === "running"; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const polled = await api.get("/api/t/reports/export", { params: { task_id: data.task_id } });
    data = polled.data.data;
  }
  if (data.status === "done" && data.download_token) {
    const file = await api.get("/api/t/reports/export", {
      params: { task_id: data.task_id, download: 1, token: data.download_token },
      responseType: "blob",
    });
    const url = URL.createObjectURL(file.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `report-${data.task_id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    exportNote.value = `报表已下载，任务 ${data.task_id}，15 分钟内有效`;
    return;
  }
  exportNote.value = `导出任务 ${data.task_id} 仍在生成`;
}

async function load(attempt) {
  status.value = "loading";
  stale.value = "";
  const cacheKey = `cache:${route.path}`;
  try {
    const response = await api.get(route.meta.endpoint, { params: { page: 1, page_size: 20 } });
    const payload = response.data.data;
    const list = Array.isArray(payload) ? payload : payload.items || [payload];
    items.value = list.filter((item) => item && typeof item === "object");
    status.value = items.value.length ? "ready" : "empty";
    localStorage.setItem(cacheKey, JSON.stringify({ at: new Date().toISOString(), items: items.value }));
  } catch (err) {
    if (err.code === "ECONNABORTED" && attempt < 1) {
      await load(attempt + 1);
      return;
    }
    if (err.code === "ECONNABORTED") {
      status.value = "timeout";
      message.value = "请求超时，已重试 1 次仍失败";
      return;
    }
    if (err.response?.status === 403) {
      status.value = "forbidden";
      return;
    }
    const cached = localStorage.getItem(cacheKey);
    if (cached) {
      const parsed = JSON.parse(cached);
      items.value = parsed.items;
      stale.value = parsed.at;
      status.value = items.value.length ? "ready" : "empty";
      return;
    }
    status.value = "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

watch(() => route.fullPath, () => load(0), { immediate: true });
</script>

<style scoped>
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.metric {
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 12px;
}
.metric span {
  display: block;
  color: #909399;
  font-size: 12px;
}
.metric strong {
  display: block;
  margin-top: 6px;
  font-size: 20px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.metric strong.formula {
  font-size: 13px;
  line-height: 1.5;
  font-weight: 400;
}
@media (max-width: 800px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
