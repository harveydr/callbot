<template>
  <div>
    <PageHeader title="平台指标" description="全局租户与呼叫规模。" />
    <StateView :status="status" :message="message" @retry="load">
      <div class="ed-metric-grid">
        <div class="ed-metric"><span>租户数</span><strong>{{ data.tenants ?? 0 }}</strong></div>
        <div class="ed-metric"><span>呼叫量</span><strong>{{ data.calls ?? 0 }}</strong></div>
        <div class="ed-metric"><span>外呼开关</span><strong>{{ data.calling_enabled ? "开启" : "关闭" }}</strong></div>
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
const data = ref({});

async function load() {
  status.value = "loading";
  try {
    const response = await api.get("/api/p/metrics");
    data.value = response.data.data;
    status.value = "ready";
  } catch (err) {
    status.value = "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load();
</script>