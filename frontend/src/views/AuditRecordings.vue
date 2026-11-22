<template>
  <div>
    <PageHeader title="录音稽核" description="通话列表（后四位不单独返回时展示 ID 与状态）。" />
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="id" label="通话 ID" min-width="220" />
        <el-table-column label="状态" width="130">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="callStatusLabels" /></template>
        </el-table-column>
        <el-table-column prop="block_reason" label="拦截原因" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/agent/calls/${row.id}`)">详情</el-button>
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
  </div>
</template>

<script setup>
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { useListFetch } from "../composables/useListFetch";
import { callStatusLabels } from "../utils/enums";

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/calls");
</script>