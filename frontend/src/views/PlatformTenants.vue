<template>
  <div>
    <PageHeader title="租户审核" description="查看租户状态，挂起/恢复服务。" />
    <StateView :status="status" :message="message" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="code" label="编码" width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="tenantStatusLabels" /></template>
        </el-table-column>
        <el-table-column label="演示" width="80">
          <template #default="{ row }">{{ row.is_demo ? "是" : "否" }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" link type="warning" @click="suspend(row)">挂起</el-button>
            <el-button v-if="row.status === 'suspended'" link type="success" @click="resume(row)">恢复</el-button>
          </template>
        </el-table-column>
      </el-table>
    </StateView>
    <el-alert v-if="note" :title="note" type="success" :closable="false" style="margin-top: 12px" />
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { useListFetch } from "../composables/useListFetch";
import { tenantStatusLabels } from "../utils/enums";

const note = ref("");
const { status, message, items, load } = useListFetch("/api/p/tenants");

async function suspend(row) {
  try {
    await api.post(`/api/p/tenants/${row.id}/suspend`);
    note.value = `租户 ${row.name} 已挂起`;
    load(0);
  } catch (err) {
    note.value = err.response?.data?.message || "操作失败";
  }
}

async function resume(row) {
  try {
    await api.post(`/api/p/tenants/${row.id}/resume`);
    note.value = `租户 ${row.name} 已恢复`;
    load(0);
  } catch (err) {
    note.value = err.response?.data?.message || "操作失败";
  }
}
</script>