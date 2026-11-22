<template>
  <div>
    <PageHeader title="合规事件" description="呼中/呼后触发的合规审计事件。" />
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="event_type" label="事件类型" width="180" />
        <el-table-column label="详情">
          <template #default="{ row }"><code>{{ formatDetail(row.detail) }}</code></template>
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
import { useListFetch } from "../composables/useListFetch";

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch(
  "/api/t/compliance/events",
);

function formatDetail(detail) {
  if (!detail) return "—";
  if (typeof detail === "string") return detail;
  return JSON.stringify(detail);
}
</script>