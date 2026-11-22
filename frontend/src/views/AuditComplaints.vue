<template>
  <div>
    <PageHeader
      title="投诉登记与调证"
      description="登记当前工单并管理 SLA。后端暂无投诉列表接口，刷新后无法恢复历史工单。"
    />
    <el-row :gutter="16">
      <el-col :md="12" :span="24">
        <section class="ed-card">
          <h3>登记投诉</h3>
          <el-form label-position="top" @submit.prevent="createComplaint">
            <el-form-item label="手机号"><el-input v-model="phone" /></el-form-item>
            <el-form-item label="投诉内容"><el-input v-model="detail" type="textarea" rows="3" /></el-form-item>
            <el-button type="primary" native-type="submit">登记并写入禁呼</el-button>
          </el-form>
        </section>
      </el-col>
      <el-col v-if="ticket" :md="12" :span="24">
        <section class="ed-card">
          <h3>当前工单</h3>
          <p>状态：<EnumTag :value="ticket.status" :labels="complaintStatusLabels" /></p>
          <p v-if="ticket.status !== 'closed'">
            SLA：需在 <strong>{{ respondLabel }}</strong> 前响应，<strong>{{ resolveLabel }}</strong> 前结案
          </p>
          <el-progress
            v-if="ticket.status !== 'closed'"
            :percentage="slaPct"
            :status="slaPct > 85 ? 'exception' : ''"
            style="margin: 8px 0"
          />
          <el-form v-if="ticket.status !== 'closed'" label-position="top" @submit.prevent="closeComplaint">
            <el-form-item label="处置结果"><el-input v-model="disposition" /></el-form-item>
            <el-button type="primary" native-type="submit">反馈并结案</el-button>
          </el-form>
          <p v-else>已结案：{{ ticket.disposition }}</p>
        </section>
      </el-col>
    </el-row>
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
    <el-button style="margin-top: 12px" @click="confirming = true">导出调证包</el-button>
    <el-dialog v-model="confirming" title="导出调证包（需密码确认）" width="420px">
      <el-input v-model="password" type="password" show-password placeholder="当前密码" />
      <template #footer>
        <el-button @click="confirming = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="exportPack">确认导出并下载</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { complaintStatusLabels } from "../utils/enums";

const phone = ref("");
const detail = ref("");
const disposition = ref("");
const password = ref("");
const confirming = ref(false);
const exporting = ref(false);
const ticket = ref(null);
const error = ref("");

function shanghai(iso) {
  return new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

const respondLabel = computed(() => (ticket.value ? shanghai(ticket.value.respond_due_at) : ""));
const resolveLabel = computed(() => (ticket.value ? shanghai(ticket.value.resolve_due_at) : ""));

const slaPct = computed(() => {
  if (!ticket.value?.respond_due_at || !ticket.value?.created_at) return 0;
  const start = Date.parse(ticket.value.created_at);
  const end = Date.parse(ticket.value.resolve_due_at);
  const now = Date.now();
  if (end <= start) return 100;
  return Math.min(100, Math.round(((now - start) / (end - start)) * 100));
});

async function createComplaint() {
  error.value = "";
  try {
    const response = await api.post(
      "/api/t/complaints",
      { phone: phone.value, detail: detail.value },
      { headers: { "Idempotency-Key": crypto.randomUUID() } },
    );
    ticket.value = response.data.data;
  } catch (exc) {
    error.value = exc.response?.data?.message || "登记失败";
  }
}

async function closeComplaint() {
  error.value = "";
  try {
    const response = await api.patch(`/api/t/complaints/${ticket.value.id}`, {
      version: ticket.value.version,
      status: "closed",
      disposition: disposition.value,
    });
    ticket.value = response.data.data;
  } catch (exc) {
    error.value = exc.response?.data?.message || "结案失败";
  }
}

async function exportPack() {
  exporting.value = true;
  error.value = "";
  try {
    const response = await api.post("/api/t/audit/evidence-pack", { password: password.value, confirm: true });
    const token = response.data.data.token;
    const pack = await api.get(`/api/t/audit/evidence-pack/${token}`, { responseType: "blob" });
    const url = URL.createObjectURL(pack.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `evidence-${token.slice(0, 8)}.zip`;
    link.click();
    URL.revokeObjectURL(url);
    confirming.value = false;
  } catch (exc) {
    error.value = exc.response?.data?.message || "导出失败";
  } finally {
    exporting.value = false;
  }
}
</script>