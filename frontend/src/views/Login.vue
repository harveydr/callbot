<template>
  <el-card style="max-width: 420px; margin: 48px auto">
    <h1>登录 EchoDesk</h1>
    <el-form @submit.prevent="submit">
      <el-form-item label="账号"><el-input v-model="username" /></el-form-item>
      <el-form-item label="密码"><el-input v-model="password" type="password" show-password /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-button type="primary" native-type="submit" :loading="loading" style="margin-top: 12px">登录</el-button>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import { useSession } from "../stores/session";

const username = ref("");
const password = ref("");
const error = ref("");
const loading = ref(false);
const router = useRouter();
const session = useSession();

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    const response = await api.post("/api/auth/login", { username: username.value, password: password.value });
    session.save({ ...response.data.data, username: username.value });
    router.push(session.mustChangePassword ? "/force-password" : session.role === "platform_admin" ? "/p/tenants" : "/t/dashboard");
  } catch (err) {
    error.value = err.response?.data?.message || "无法连接服务，请确认控制台和接口使用同一访问地址";
  } finally {
    loading.value = false;
  }
}
</script>
