<template>
  <div>
    <PageHeader title="控制台" description="租户运营概览（基于报表 overview 接口）。" />
    <StateView :status="status" :message="message" @retry="load">
      <div class="ed-metric-grid">
        <div v-for="m in primaryMetrics" :key="m.key" class="ed-metric">
          <span>{{ m.label }}</span>
          <strong>{{ format(m.key, data[m.key]) }}</strong>
        </div>
      </div>
      <h3 style="margin-top: 20px">接通与意向</h3>
      <div class="ed-metric-grid">
        <div v-for="m in intentMetrics" :key="m.key" class="ed-metric">
          <span>{{ m.label }}</span>
          <strong>{{ format(m.key, data[m.key]) }}</strong>
        </div>
      </div>
      <h3 style="margin-top: 20px">合规拦截</h3>
      <div class="ed-metric-grid">
        <div class="ed-metric"><span>无授权拦截</span><strong>{{ data.no_consent_blocks ?? 0 }}</strong></div>
        <div class="ed-metric"><span>DND 拦截</span><strong>{{ data.dnd_blocks ?? 0 }}</strong></div>
        <div class="ed-metric"><span>命中最高规则</span><strong>{{ data.top_compliance_rule || "—" }}</strong></div>
      </div>
      <el-alert
        type="info"
        :closable="false"
        title="并发水位与异常任务榜：后端 overview 未提供对应字段，本版不展示。"
        style="margin-top: 16px"
      />
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
const data = ref({});

const primaryMetrics = [
  { key: "calls", label: "总呼叫" },
  { key: "completed", label: "完成" },
  { key: "blocked_compliance", label: "合规拦截" },
  { key: "answer_rate", label: "接通率" },
  { key: "reject_rate", label: "拒接率" },
  { key: "ai_complete_rate", label: "AI 独立完成率" },
  { key: "transfer_rate", label: "转人工率" },
  { key: "avg_duration_sec", label: "平均时长(秒)" },
];

const intentMetrics = [
  { key: "intent_a", label: "意向 A" },
  { key: "intent_b", label: "意向 B" },
  { key: "intent_c", label: "意向 C" },
  { key: "intent_d", label: "意向 D" },
  { key: "intent_na", label: "意向 NA" },
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

load();
</script>