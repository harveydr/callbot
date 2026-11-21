<template>
  <el-result icon="warning" title="403" sub-title="无权限访问这个页面">
    <template #extra><el-button @click="$router.push('/login')">返回登录</el-button></template>
  </el-result>
</template>
