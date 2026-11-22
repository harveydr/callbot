<template>
  <div>
    <PageHeader title="线路与号码" description="查看线路状态，支持冒烟测试与并发调整（租户管理员）。" />
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="mode" label="模式" width="100" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="trunkStatusLabels" /></template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column v-if="isAdmin" label="操作" width="200">
          <template #default="{ row }">
            <el-button link type="primary" @click="testCall(row)">冒烟测试</el-button>
            <el-button link @click="toggleStatus(row)">{{ row.status === "active" ? "停用" : "激活" }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px"
        layout="total, sizes, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="PAGE_SIZES"
        @current-change="setPage"
        @size-change="setPageSize"
      />
    </StateView>
    <el-alert v-if="actionNote" :title="actionNote" type="success" :closable="false" style="margin-top: 12px" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { useListFetch } from "../composables/useListFetch";
import { useSession } from "../stores/session";
import { trunkStatusLabels } from "../utils/enums";

const session = useSession();
const isAdmin = computed(() => session.role === "tenant_admin");
const actionNote = ref("");

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/trunks");

async function testCall(row) {
  try {
    await api.post(`/api/t/trunks/${row.id}/test-call`);
    actionNote.value = `线路 ${row.name} 冒烟测试已发起`;
  } catch (err) {
    actionNote.value = err.response?.data?.message || "测试失败";
  }
}

async function toggleStatus(row) {
  const next = row.status === "active" ? "inactive" : "active";
  try {
    await api.patch(`/api/t/trunks/${row.id}`, { version: row.version, status: next });
    actionNote.value = `线路 ${row.name} 已设为 ${trunkStatusLabels[next]}`;
    load(0);
  } catch (err) {
    actionNote.value = err.response?.data?.message || "更新失败";
  }
}
</script>