<template>
  <div>
    <el-skeleton v-if="status === 'loading'" :rows="4" animated />
    <el-empty v-else-if="status === 'empty'" :description="emptyText">
      <slot name="empty">
        <el-button type="primary" @click="$emit('retry')">刷新</el-button>
      </slot>
    </el-empty>
    <el-result v-else-if="status === 'forbidden'" icon="warning" title="无权限" sub-title="当前角色不能查看此页面" />
    <el-result v-else-if="status === 'timeout'" icon="error" title="请求超时" :sub-title="message || '已自动重试 1 次仍失败'">
      <template #extra><el-button @click="$emit('retry')">重试</el-button></template>
    </el-result>
    <el-result v-else-if="status === 'error'" icon="error" title="加载失败" :sub-title="message">
      <template #extra><el-button @click="$emit('retry')">重试</el-button></template>
    </el-result>
    <div v-else>
      <el-alert v-if="stale" :title="`离线缓存数据，更新于 ${stale}`" type="warning" show-icon :closable="false" style="margin-bottom: 12px" />
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  status: { type: String, required: true },
  message: { type: String, default: "" },
  stale: { type: String, default: "" },
  emptyText: { type: String, default: "暂无数据" },
});
defineEmits(["retry"]);
</script>