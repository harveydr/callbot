<template>
  <router-view v-if="!session.accessToken" />
  <el-container v-else class="shell">
    <el-aside :width="collapsed ? '64px' : '220px'" class="sidebar">
      <div class="brand" @click="collapsed = !collapsed">
        <strong v-if="!collapsed">EchoDesk</strong>
        <span v-else>ED</span>
      </div>
      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="route.path"
          :collapse="collapsed"
          :router="true"
          background-color="var(--ed-sidebar-bg)"
          text-color="var(--ed-sidebar-text)"
          active-text-color="#ffffff"
        >
          <template v-for="group in groups" :key="group.title">
            <div v-if="!collapsed" class="group-title">{{ group.title }}</div>
            <el-menu-item v-for="item in group.items" :key="item.to" :index="item.to">
              {{ item.label }}
            </el-menu-item>
          </template>
        </el-menu>
      </el-scrollbar>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item :to="session.homePath()">首页</el-breadcrumb-item>
          <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="topbar-right">
          <span v-if="session.tenantId" class="ed-muted">租户 {{ session.tenantId }}</span>
          <span>{{ roleLabel(session.role) }} · {{ session.username || "未命名用户" }}</span>
          <el-button link type="danger" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="ed-page">
        <el-alert
          v-if="degraded"
          title="服务降级：全局外呼已暂停。合规拦截仍生效，新外呼不会发起。"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-alert
          v-else-if="voiceMode === '语音合成暂停'"
          title="语音合成不可用，新外呼已暂停，系统会稍后回拨。"
          type="error"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-alert
          v-else-if="voiceMode === '话术树模式'"
          title="话术树模式：按标准流程应答，大模型自由话术已降级。"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "./api/client";
import { useSession } from "./stores/session";
import { navGroups, roleLabel, routeTitles } from "./utils/navigation";

const session = useSession();
const router = useRouter();
const route = useRoute();
const collapsed = ref(false);
const degraded = ref(false);
const voiceMode = ref("正常");
session.hydrate();

const groups = computed(() => navGroups(session.role));
const currentTitle = computed(() => route.meta.title || routeTitles[route.path] || "");

watch(
  () => route.fullPath,
  () => {
    const title = currentTitle.value;
    document.title = title ? `${title} · EchoDesk` : "EchoDesk";
  },
  { immediate: true },
);

onMounted(async () => {
  if (!session.accessToken || session.role === "platform_admin") return;
  try {
    const response = await api.get("/api/t/reports/overview");
    degraded.value = Boolean(response.data.data.degraded);
    voiceMode.value = response.data.data.voice_mode || "正常";
  } catch {
    degraded.value = false;
    voiceMode.value = "正常";
  }
});

async function logout() {
  try {
    if (session.accessToken) await api.post("/api/auth/logout");
  } catch {
    /* ignore */
  }
  session.clear();
  router.push("/login");
}
</script>

<style scoped>
.shell {
  min-height: 100vh;
}
.sidebar {
  background: var(--ed-sidebar-bg);
  color: #fff;
  transition: width 0.2s ease;
}
.brand {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}
.menu-scroll {
  height: calc(100vh - 56px);
}
.group-title {
  padding: 12px 20px 4px;
  font-size: 12px;
  color: #94a3b8;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--ed-header-bg);
  border-bottom: 1px solid #ebeef5;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
</style>