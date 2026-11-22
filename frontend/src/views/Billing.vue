<template>
  <div>
    <PageHeader title="账单与用量" description="月度汇总与计费状态。" />
    <StateView :status="status" :message="message" :stale="stale" @retry="load(0)">
      <div v-if="summary" class="ed-metric-grid">
        <div v-for="(value, key) in summary" :key="key" class="ed-metric">
          <span>{{ label(key) }}</span>
          <strong>{{ format(key, value) }}</strong>
        </div>
      </div>
    </StateView>
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";

const status = ref("loading");
const message = ref("");
const stale = ref("");
const summary = ref(null);

const labels = {
  month: "账期",
  amount: "金额",
  currency: "币种",
  month_quoted: "本月已报价",
  month_unquoted: "本月未报价笔数",
  billing_budget: "月度上限",
  cost_per_call: "每通成本",
  cost_per_intent: "每意向成本",
  cost_status: "成本状态",
  formula: "计费公式",
  new_tasks_paused: "新任务已暂停",
};

function label(key) {
  return labels[key] || key;
}

function format(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (key === "cost_status") {
    const map = { unquoted: "未报价", usage_unmetered: "用量未计量", no_calls: "本月无接通", quoted: "已核算" };
    return map[value] || value;
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  return value;
}

async function load(attempt = 0) {
  status.value = "loading";
  stale.value = "";
  try {
    const response = await api.get("/api/t/billing/summary");
    summary.value = response.data.data;
    status.value = "ready";
    localStorage.setItem("cache:/t/billing", JSON.stringify({ at: new Date().toISOString(), items: [summary.value] }));
  } catch (err) {
    if (err.code === "ECONNABORTED" && attempt < 1) {
      await load(attempt + 1);
      return;
    }
    if (err.code === "ECONNABORTED") {
      status.value = "timeout";
      message.value = "请求超时";
      return;
    }
    if (err.response?.status === 403) {
      status.value = "forbidden";
      return;
    }
    const cached = localStorage.getItem("cache:/t/billing");
    if (cached) {
      summary.value = JSON.parse(cached).items[0];
      stale.value = JSON.parse(cached).at;
      status.value = "ready";
      return;
    }
    status.value = "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load(0);
</script>