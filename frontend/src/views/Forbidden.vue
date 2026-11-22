<template>
  <el-result icon="warning" title="无权限" sub-title="当前账号不能访问该页面">
    <template #extra>
      <el-button type="primary" @click="$router.push(home)">返回首页</el-button>
      <el-button @click="$router.push('/login')">切换账号</el-button>
    </template>
  </el-result>
</template>

<script setup>
import { computed } from "vue";
import { useSession } from "../stores/session";

const session = useSession();
session.hydrate();
const home = computed(() => session.homePath());
</script>