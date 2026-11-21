<template>
  <el-container style="min-height: 100vh">
    <el-header v-if="session.accessToken" height="56px" style="display: flex; align-items: center; gap: 16px">
      <strong>EchoDesk</strong>
      <el-menu mode="horizontal" :router="true" :ellipsis="false" style="flex: 1; border-bottom: none">
        <el-menu-item v-for="item in links" :key="item.to" :index="item.to">{{ item.label }}</el-menu-item>
      </el-menu>
      <span>{{ session.role }}</span>
      <el-button link @click="logout">退出</el-button>
    </el-header>
    <el-main>
      <el-alert
        v-if="degraded"
        title="服务降级：全局外呼已暂停。进行中的合规拦截仍然生效，新的外呼不会发起。"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "./api/client";
import { useSession } from "./stores/session";

const session = useSession();
const router = useRouter();
const degraded = ref(false);
session.hydrate();

const links = computed(() => {
  if (session.role === "platform_admin") {
    return [
      { to: "/p/tenants", label: "租户" },
      { to: "/p/trunks", label: "熔断" },
      { to: "/p/policies", label: "告警" },
      { to: "/p/billing", label: "指标" },
    ];
  }
  const items = [
    { to: "/t/dashboard", label: "控制台" },
    { to: "/t/campaigns", label: "任务" },
    { to: "/t/lists", label: "名单" },
    { to: "/t/templates", label: "话术" },
    { to: "/t/live", label: "实时" },
  ];
  if (session.role === "tenant_admin") {
    items.push({ to: "/t/billing", label: "账单" }, { to: "/t/members", label: "成员" });
  }
  if (session.role === "auditor" || session.role === "tenant_admin") {
    items.push({ to: "/t/audit/complaints", label: "合规" });
  }
  if (session.role === "agent") items.push({ to: "/agent/queue", label: "我的来电" });
  return items;
});

onMounted(async () => {
  if (!session.accessToken || session.role === "platform_admin") return;
  try {
    const response = await api.get("/api/t/reports/overview");
    degraded.value = Boolean(response.data.data.degraded);
  } catch {
    degraded.value = false;
  }
});

function logout() {
  session.clear();
  router.push("/login");
}
</script>
