<template>
  <div>
    <PageHeader title="话术工作室" description="选择已发布模板编辑节点（暂无创建模板 API）。">
      <template #actions>
        <el-input v-model="search" placeholder="搜索名称/场景" clearable style="width: 220px" @input="onSearch" />
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" :stale="stale" empty-text="暂无话术模板" @retry="() => load(0)">
      <template #empty>
        <p class="ed-muted">请联系管理员发布模板，或确认租户已有 approved 模板。</p>
        <el-button @click="load(0)">刷新</el-button>
      </template>
      <el-table :data="items" row-key="id" @row-click="open">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="scenario_code" label="场景" width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><EnumTag :value="row.status" :labels="templateStatusLabels" /></template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="80" />
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
import { templateStatusLabels } from "../utils/enums";

const router = useRouter();
const {
  PAGE_SIZES,
  status,
  message,
  stale,
  items,
  total,
  page,
  pageSize,
  search,
  load,
  onSearch,
  setPage,
  setPageSize,
} = useListFetch("/api/t/templates", {
  clientFilter: (row, q) =>
    String(row.name || "").toLowerCase().includes(q) || String(row.scenario_code || "").toLowerCase().includes(q),
});

function open(row) {
  router.push(`/t/templates/${row.id}/nodes`);
}
</script>