<template>
  <el-card style="max-width: 480px; margin: 48px auto">
    <h1>首次登录请修改密码</h1>
    <el-form @submit.prevent="submit">
      <el-form-item label="当前密码"><el-input v-model="currentPassword" type="password" show-password /></el-form-item>
      <el-form-item label="新密码"><el-input v-model="nextPassword" type="password" show-password /></el-form-item>
      <el-button type="primary" native-type="submit">保存并继续</el-button>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import { useSession } from "../stores/session";

const currentPassword = ref("");
const nextPassword = ref("");
const router = useRouter();
const session = useSession();

async function submit() {
  await api.post("/api/auth/change-password", {
    current_password: currentPassword.value,
    new_password: nextPassword.value,
  });
  session.mustChangePassword = false;
  session.save({
    access_token: session.accessToken,
    role: session.role,
    tenant_id: session.tenantId,
    must_change_password: false,
    username: session.username,
  });
  router.push("/t/dashboard");
}
</script>
