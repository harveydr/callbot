<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="hover">
      <div class="login-brand">
        <h1>EchoDesk</h1>
        <p class="ed-muted">多租户 AI 语音外呼控制台</p>
      </div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="username" autocomplete="username" placeholder="请输入账号" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password autocomplete="current-password" placeholder="请输入密码" />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
        <el-alert
          v-if="lockedUntil"
          :title="`账号已锁定，将于 ${lockedLabel} 后自动解锁`"
          type="warning"
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import { roleHome } from "../utils/navigation";

const username = ref("");
const password = ref("");
const error = ref("");
const loading = ref(false);
const lockedUntil = ref("");
const router = useRouter();
const session = useSession();

const lockedLabel = computed(() => {
  if (!lockedUntil.value) return "";
  return new Date(lockedUntil.value).toLocaleString("zh-CN", { hour12: false });
});

let lockTimer = 0;

function tickLock() {
  if (!lockedUntil.value) return;
  const ms = Date.parse(lockedUntil.value) - Date.now();
  if (ms <= 0) {
    lockedUntil.value = "";
    error.value = "";
    window.clearInterval(lockTimer);
  }
}

onMounted(() => {
  lockTimer = window.setInterval(tickLock, 1000);
});

onBeforeUnmount(() => window.clearInterval(lockTimer));

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    const response = await api.post("/api/auth/login", { username: username.value, password: password.value });
    session.save({ ...response.data.data, username: username.value });
    const dest = session.mustChangePassword ? "/force-password" : roleHome(session.role);
    router.push(dest);
  } catch (err) {
    if (err.response?.status === 423) {
      lockedUntil.value = err.response.data?.data?.unlock_at || "";
      error.value = err.response.data?.message || "账号已锁定";
    } else {
      error.value = err.response?.data?.message || "无法连接服务，请确认控制台和接口使用同一访问地址";
    }
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #eff6ff, #f8fafc);
}
.login-card {
  width: min(420px, 92vw);
}
.login-brand h1 {
  margin: 0;
  font-size: 26px;
}
.login-brand {
  margin-bottom: 20px;
}
</style>