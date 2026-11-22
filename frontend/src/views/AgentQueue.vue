<template>
  <div>
    <PageHeader title="我的来电" description="点击进入通话小结（使用 /calls/:id 详情接口）。" />
    <StateView :status="status" :message="message" :stale="stale" empty-text="暂无分配通话" @retry="() => load(0)">
      <el-table :data="items" row-key="id" @row-click="open" @row-dblclick="open">
        <el-table-column label="通话 ID" min-width="200">
          <template #default="{ row }">
            <span>{{ row.id }}</span>
            <span class="ed-muted">（尾号 {{ tail(row.id) }}）</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="callStatusLabels" /></template>
        </el-table-column>
        <el-table-column prop="block_reason" label="拦截原因" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="open(row)">小结</el-button>
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
import { useRouter } from "vue-router";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { useListFetch } from "../composables/useListFetch";
import { callStatusLabels } from "../utils/enums";

const router = useRouter();
const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/calls");

function tail(id) {
  return String(id).replace(/-/g, "").slice(-4);
}

function open(row) {
  router.push(`/agent/calls/${row.id}`);
}
</script>