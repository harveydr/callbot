<template>
  <div>
    <PageHeader title="全局告警" description="平台级策略与告警事件。" />
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="severity" label="级别" width="100" />
        <el-table-column prop="code" label="代码" width="160" />
        <el-table-column prop="message" label="内容" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { useListFetch } from "../composables/useListFetch";

const { status, message, stale, items, load } = useListFetch("/api/p/alerts");
</script>