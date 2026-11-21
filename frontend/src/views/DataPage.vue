<template>
  <div>
    <h1>{{ title }}</h1>
    <el-button v-if="canExport" type="primary" @click="exportReport">导出明细</el-button>
    <el-alert v-if="exportNote" :title="exportNote" type="success" show-icon :closable="false" style="margin: 12px 0" />
    <StateView :status="status" :message="message" :stale="stale" @retry="load(0)">
      <el-table :data="items" style="width: 100%">
        <el-table-column v-for="column in columns" :key="column" :prop="column" :label="column" />
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
const columns = computed(() => Object.keys(items.value[0] || {}));

async function exportReport() {
  try {
    await ElMessageBox.confirm("导出包含通话明细，链接 15 分钟内有效。确认导出？", "导出确认");
  } catch {
    return;
  }
  const response = await api.get("/api/t/reports/export");
  exportNote.value = `已生成导出任务 ${response.data.data.task_id}，15 分钟内有效`;
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
