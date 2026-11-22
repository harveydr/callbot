<template>
  <div>
    <PageHeader title="熔断状态" description="租户级熔断看板；复位需双人密码确认（PRD）。" />
    <StateView :status="status" :message="message" @retry="load">
      <el-alert :title="`全局外呼开关：${data.calling_enabled ? '开启' : '关闭'}`" :type="data.calling_enabled ? 'success' : 'error'" :closable="false" />
      <el-table :data="data.items || []" style="margin-top: 12px">
        <el-table-column prop="tenant_id" label="租户 ID" min-width="220" />
        <el-table-column label="熔断" width="100">
          <template #default="{ row }"><el-tag :type="row.opened ? 'danger' : 'success'" size="small">{{ row.opened ? "已熔断" : "正常" }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button v-if="row.opened" link type="primary" @click="openReset(row)">复位</el-button>
          </template>
        </el-table-column>
      </el-table>
    </StateView>
    <el-dialog v-model="showReset" title="熔断复位（双人确认）" width="520px">
      <el-form label-position="top">
        <el-form-item label="原因" required><el-input v-model="resetForm.reason" type="textarea" rows="2" /></el-form-item>
        <el-form-item label="复核管理员账号" required><el-input v-model="resetForm.reviewer_username" /></el-form-item>
        <el-form-item label="复核管理员密码" required><el-input v-model="resetForm.reviewer_password" type="password" show-password /></el-form-item>
        <el-checkbox v-model="resetForm.confirm">我确认已完成风险评估</el-checkbox>
      </el-form>
      <el-alert v-if="resetError" :title="resetError" type="error" :closable="false" />
      <template #footer>
        <el-button @click="showReset = false">取消</el-button>
        <el-button type="danger" @click="resetCircuit">确认复位</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";

const status = ref("loading");
const message = ref("");
const data = ref({ calling_enabled: false, items: [] });
const showReset = ref(false);
const resetTarget = ref("");
const resetError = ref("");
const resetForm = ref({ reason: "", reviewer_username: "", reviewer_password: "", confirm: false });

async function load() {
  status.value = "loading";
  try {
    const response = await api.get("/api/p/circuit-status");
    data.value = response.data.data;
    status.value = "ready";
  } catch (err) {
    status.value = "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

function openReset(row) {
  resetTarget.value = row.tenant_id;
  resetForm.value = { reason: "", reviewer_username: "", reviewer_password: "", confirm: false };
  resetError.value = "";
  showReset.value = true;
}

async function resetCircuit() {
  if (!resetForm.value.confirm || !resetForm.value.reason.trim()) {
    resetError.value = "请填写原因并勾选确认";
    return;
  }
  try {
    await api.post(`/api/p/circuit/${resetTarget.value}/reset`, {
      reason: resetForm.value.reason,
      reviewer_username: resetForm.value.reviewer_username,
      reviewer_password: resetForm.value.reviewer_password,
      confirm: true,
    });
    showReset.value = false;
    load();
  } catch (err) {
    resetError.value = err.response?.data?.message || "复位失败";
  }
}

load();
</script>