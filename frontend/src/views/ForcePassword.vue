<template>
  <el-card style="max-width: 480px; margin: 48px auto">
    <PageHeader title="首次登录请修改密码" description="新密码需与当前密码不同，保存后按角色进入对应首页。" />
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="当前密码">
        <el-input v-model="currentPassword" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="nextPassword" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
      <el-button type="primary" native-type="submit" :loading="loading">保存并继续</el-button>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import PageHeader from "../components/PageHeader.vue";

const currentPassword = ref("");
const nextPassword = ref("");
const error = ref("");
const loading = ref(false);
const router = useRouter();
const session = useSession();

async function submit() {
  if (!nextPassword.value || nextPassword.value.length < 8) {
    error.value = "新密码至少 8 位";
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    await api.post("/api/auth/change-password", {
      current_password: currentPassword.value,
      new_password: nextPassword.value,
    });
    session.mustChangePassword = false;
    session.save({
      access_token: session.accessToken,
      refresh_token: session.refreshToken,
      expires_in: session.expiresIn,
      role: session.role,
      tenant_id: session.tenantId,
      must_change_password: false,
      username: session.username,
    });
    router.push(session.homePath());
  } catch (err) {
    error.value = err.response?.data?.message || "修改失败，请检查当前密码";
  } finally {
    loading.value = false;
  }
}
</script>